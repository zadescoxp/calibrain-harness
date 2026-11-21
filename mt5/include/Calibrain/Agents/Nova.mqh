#ifndef CALIBRAIN_NOVA_MQH
#define CALIBRAIN_NOVA_MQH

#include <Calibrain/Types.mqh>

class CNovaAgent
  {
private:
   int               m_tester_day;
   double            m_tester_baseline;

   string BaselineKey(const ulong magic,const int day_key)
     {
      long login=AccountInfoInteger(ACCOUNT_LOGIN);
      return StringFormat("CB_EQ_%I64d_%I64u_%d",login,magic,day_key);
     }

   double PositionRisk(const string symbol)
     {
      double stop=PositionGetDouble(POSITION_SL);
      double open=PositionGetDouble(POSITION_PRICE_OPEN);
      double volume=PositionGetDouble(POSITION_VOLUME);
      if(stop<=0.0)
         return 1.0e100;
      double tick_size=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
      double tick_value=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
      if(tick_value<=0.0)
         tick_value=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
      if(tick_size<=0.0 || tick_value<=0.0)
         return 1.0e100;
      return MathAbs(open-stop)/tick_size*tick_value*volume;
     }

   int VolumeDigits(const double step)
     {
      for(int digits=0;digits<=8;digits++)
         if(MathAbs(step-NormalizeDouble(step,digits))<1e-10)
            return digits;
      return 8;
     }

public:
                     CNovaAgent(void)
     {
      m_tester_day=0;
      m_tester_baseline=0.0;
     }

   void EnsureDailyBaseline(const ulong magic)
     {
      MqlDateTime now;
      TimeToStruct(TimeCurrent(),now);
      int day_key=now.year*10000+now.mon*100+now.day;
      if(MQLInfoInteger(MQL_TESTER))
        {
         if(m_tester_day!=day_key || m_tester_baseline<=0.0)
           {
            m_tester_day=day_key;
            m_tester_baseline=AccountInfoDouble(ACCOUNT_EQUITY);
           }
         return;
        }
      string key=BaselineKey(magic,day_key);
      if(!GlobalVariableCheck(key))
         GlobalVariableSet(key,AccountInfoDouble(ACCOUNT_EQUITY));
     }

   double DailyDrawdownPct(const ulong magic)
     {
      MqlDateTime now;
      TimeToStruct(TimeCurrent(),now);
      int day_key=now.year*10000+now.mon*100+now.day;
      if(MQLInfoInteger(MQL_TESTER))
        {
         EnsureDailyBaseline(magic);
         if(m_tester_baseline<=0.0)
            return 0.0;
         return MathMax(0.0,(m_tester_baseline-AccountInfoDouble(ACCOUNT_EQUITY))/
                            m_tester_baseline*100.0);
        }
      string key=BaselineKey(magic,day_key);
      if(!GlobalVariableCheck(key))
         EnsureDailyBaseline(magic);
      double baseline=GlobalVariableGet(key);
      if(baseline<=0.0)
         return 0.0;
      return MathMax(0.0,(baseline-AccountInfoDouble(ACCOUNT_EQUITY))/baseline*100.0);
     }

   bool Approve(const SMarketSnapshot &market,
                const int direction,
                const double entry_price,
                const double stop_loss,
                const ulong magic,
                const double risk_per_trade_pct,
                const double maximum_portfolio_risk_pct,
                const double maximum_daily_loss_pct,
                const int maximum_open_positions,
                const double maximum_free_margin_use_pct,
                double &volume,
                double &risk_money,
                string &reason)
     {
      volume=0.0;
      risk_money=0.0;
      EnsureDailyBaseline(magic);
      double equity=AccountInfoDouble(ACCOUNT_EQUITY);
      if(equity<=0.0)
        {
         reason="Account equity is unavailable";
         return false;
        }
      if(DailyDrawdownPct(magic)>=maximum_daily_loss_pct)
        {
         reason="Daily equity drawdown limit reached; new entries are disabled";
         return false;
        }

      int open_count=0;
      double portfolio_risk=0.0;
      for(int i=0;i<PositionsTotal();i++)
        {
         ulong ticket=PositionGetTicket(i);
         if(ticket==0 || !PositionSelectByTicket(ticket))
            continue;
         string position_symbol=PositionGetString(POSITION_SYMBOL);
         if(position_symbol==market.symbol)
           {
            volume=0.0;
            reason="A position already exists on this symbol; refusing to mix trade ownership";
            return false;
           }
         if((ulong)PositionGetInteger(POSITION_MAGIC)!=magic)
            continue;
         open_count++;
         portfolio_risk+=PositionRisk(position_symbol);
        }
      if(open_count>=maximum_open_positions)
        {
         reason="Maximum open positions for this EA have been reached";
         return false;
        }

      double distance=MathAbs(entry_price-stop_loss);
      double tick_size=SymbolInfoDouble(market.symbol,SYMBOL_TRADE_TICK_SIZE);
      double tick_value=SymbolInfoDouble(market.symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
      if(tick_value<=0.0)
         tick_value=SymbolInfoDouble(market.symbol,SYMBOL_TRADE_TICK_VALUE);
      if(distance<=0.0 || tick_size<=0.0 || tick_value<=0.0)
        {
         reason="Stop distance or symbol tick-value metadata is invalid";
         return false;
        }

      double risk_per_lot=distance/tick_size*tick_value;
      double risk_budget=equity*risk_per_trade_pct/100.0;
      if(risk_per_lot<=0.0 || risk_budget<=0.0)
        {
         reason="Risk budget or per-lot stop risk is invalid";
         return false;
        }
      double raw_volume=risk_budget/risk_per_lot;
      double step=SymbolInfoDouble(market.symbol,SYMBOL_VOLUME_STEP);
      double minimum=SymbolInfoDouble(market.symbol,SYMBOL_VOLUME_MIN);
      double maximum=SymbolInfoDouble(market.symbol,SYMBOL_VOLUME_MAX);
      if(step<=0.0 || minimum<=0.0 || maximum<minimum)
        {
         reason="Broker volume constraints are invalid";
         return false;
        }
      volume=NormalizeDouble(MathFloor(raw_volume/step)*step,VolumeDigits(step));
      if(volume<minimum)
        {
         volume=0.0;
         reason="Minimum broker lot would exceed the per-trade risk budget";
         return false;
        }
      volume=MathMin(volume,maximum);
      risk_money=volume*risk_per_lot;
      if((portfolio_risk+risk_money)/equity*100.0>maximum_portfolio_risk_pct)
        {
         volume=0.0;
         reason="Portfolio stop-risk cap would be exceeded";
         return false;
        }

      ENUM_ORDER_TYPE order_type=(direction>0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      double price=(direction>0 ? market.ask : market.bid);
      double margin=0.0;
      if(!OrderCalcMargin(order_type,market.symbol,volume,price,margin))
        {
         volume=0.0;
         reason="Broker could not calculate required margin";
         return false;
        }
      if(margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)*maximum_free_margin_use_pct/100.0)
        {
         volume=0.0;
         reason="Required margin exceeds the configured free-margin allowance";
         return false;
        }

      reason=StringFormat("Risk approved: %.2f%% equity; portfolio stop-risk %.2f%%",
                          risk_money/equity*100.0,(portfolio_risk+risk_money)/equity*100.0);
      return true;
     }
  };

#endif
