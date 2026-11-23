#ifndef CALIBRAIN_LUNA_MQH
#define CALIBRAIN_LUNA_MQH

#include <Trade/Trade.mqh>
#include <Calibrain/Logger.mqh>

class CLunaAgent
  {
private:
   double NormalizePrice(const string symbol,const double price)
     {
      int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
      return NormalizeDouble(price,digits);
     }

public:
   void Manage(CTrade &trade,
               CCalibrainLogger &logger,
               const string symbol,
               const ulong magic,
               const double atr,
               const double break_even_r,
               const double trail_start_r,
               const double trail_atr,
               const double break_even_offset_points,
               const double reward_risk)
     {
      if(atr<=0.0)
         return;
      for(int i=PositionsTotal()-1;i>=0;i--)
        {
         ulong ticket=PositionGetTicket(i);
         if(ticket==0 || !PositionSelectByTicket(ticket))
            continue;
         if(PositionGetString(POSITION_SYMBOL)!=symbol ||
            (ulong)PositionGetInteger(POSITION_MAGIC)!=magic)
            continue;

         ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         double entry=PositionGetDouble(POSITION_PRICE_OPEN);
         double stop=PositionGetDouble(POSITION_SL);
         double target=PositionGetDouble(POSITION_TP);
         if(stop<=0.0 || target<=0.0)
            continue;

         double initial_risk=MathAbs(target-entry)/reward_risk;
         if(initial_risk<=0.0)
            continue;
         double bid=SymbolInfoDouble(symbol,SYMBOL_BID);
         double ask=SymbolInfoDouble(symbol,SYMBOL_ASK);
         double point=SymbolInfoDouble(symbol,SYMBOL_POINT);
         double current=(type==POSITION_TYPE_BUY ? bid : ask);
         double reward=(type==POSITION_TYPE_BUY ? current-entry : entry-current);
         double open_r=reward/initial_risk;
         double candidate=stop;
         bool should_modify=false;

         if(break_even_r>0.0 && open_r>=break_even_r)
           {
            double be=(type==POSITION_TYPE_BUY ?
                       entry+break_even_offset_points*point :
                       entry-break_even_offset_points*point);
            if(type==POSITION_TYPE_BUY && be>candidate && be<bid)
              {
               candidate=be;
               should_modify=true;
              }
            if(type==POSITION_TYPE_SELL && be<candidate && be>ask)
              {
               candidate=be;
               should_modify=true;
              }
           }

         if(trail_start_r>0.0 && trail_atr>0.0 && open_r>=trail_start_r)
           {
            double trailing=(type==POSITION_TYPE_BUY ? bid-atr*trail_atr :
                                                        ask+atr*trail_atr);
            if(type==POSITION_TYPE_BUY && trailing>candidate && trailing<bid)
              {
               candidate=trailing;
               should_modify=true;
              }
            if(type==POSITION_TYPE_SELL && trailing<candidate && trailing>ask)
              {
               candidate=trailing;
               should_modify=true;
              }
           }

         if(should_modify)
           {
            if(trade.PositionModify(ticket,NormalizePrice(symbol,candidate),target))
               logger.Write("INFO",symbol,"Luna",
                            StringFormat("Stop advanced to %.5f at %.2fR",candidate,open_r));
            else
               logger.Write("ERROR",symbol,"Luna",
                            StringFormat("Stop modification failed, retcode %u: %s",
                                         trade.ResultRetcode(),trade.ResultRetcodeDescription()));
           }
        }
     }
  };

#endif
