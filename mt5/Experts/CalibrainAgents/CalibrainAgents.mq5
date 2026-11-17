#property strict
#property version   "1.00"
#property description "Six-agent, risk-first MT5 Expert Advisor. Demo trading only."

#include <Trade/Trade.mqh>
#include <Calibrain/Types.mqh>
#include <Calibrain/Logger.mqh>
#include <Calibrain/Agents/Atlas.mqh>
#include <Calibrain/Agents/Vega.mqh>
#include <Calibrain/Agents/Orion.mqh>
#include <Calibrain/Agents/Titan.mqh>
#include <Calibrain/Agents/Luna.mqh>
#include <Calibrain/Agents/Nova.mqh>

input ENUM_TIMEFRAMES InpTimeframe=PERIOD_M15;
input bool            InpEnableTrading=false;
input ulong           InpMagicNumber=26111701;
input bool            InpUseMarketDepth=false;

input int             InpFastEma=20;
input int             InpSlowEma=50;
input int             InpRsiPeriod=14;
input int             InpAdxPeriod=14;
input int             InpAtrPeriod=14;
input double          InpMinimumAdx=18.0;
input double          InpMinimumConfidence=0.62;
input double          InpRewardRisk=1.5;

input double          InpRiskPerTradePct=0.25;
input double          InpMaximumPortfolioRiskPct=1.0;
input double          InpMaximumDailyLossPct=2.0;
input int             InpMaximumOpenPositions=3;
input double          InpMaximumFreeMarginUsePct=20.0;
input int             InpMaximumSpreadPoints=30;
input double          InpMinimumAtrPoints=10.0;
input int             InpRolloverBlockMinutes=10;
input bool            InpUseSessionFilter=false;
input int             InpSessionStartHour=7;
input int             InpSessionEndHour=20;
input int             InpDeviationPoints=10;

input double          InpBreakEvenAtR=1.0;
input double          InpTrailStartAtR=1.5;
input double          InpTrailAtrMultiple=1.0;
input double          InpBreakEvenOffsetPoints=2.0;

CTrade          g_trade;
CCalibrainLogger g_logger;
CAtlasAgent     g_atlas;
CVegaAgent      g_vega;
COrionAgent     g_orion;
CTitanAgent     g_titan;
CLunaAgent      g_luna;
CNovaAgent      g_nova;

int             g_atr_handle=INVALID_HANDLE;
int             g_fast_ema_handle=INVALID_HANDLE;
int             g_slow_ema_handle=INVALID_HANDLE;
int             g_rsi_handle=INVALID_HANDLE;
int             g_adx_handle=INVALID_HANDLE;
datetime        g_last_bar_time=0;
bool            g_market_book_subscribed=false;

double NormalizePrice(const double price)
  {
   return NormalizeDouble(price,(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS));
  }

bool ReadSnapshot(SMarketSnapshot &market)
  {
   double atr[1],fast[2],slow[1],rsi[1],adx[1];
   if(CopyBuffer(g_atr_handle,0,1,1,atr)!=1 ||
      CopyBuffer(g_fast_ema_handle,0,1,2,fast)!=2 ||
      CopyBuffer(g_slow_ema_handle,0,1,1,slow)!=1 ||
      CopyBuffer(g_rsi_handle,0,1,1,rsi)!=1 ||
      CopyBuffer(g_adx_handle,0,1,1,adx)!=1)
      return false;

   market.symbol=_Symbol;
   market.timeframe=InpTimeframe;
   market.bar_time=iTime(_Symbol,InpTimeframe,1);
   market.bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
   market.ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
   market.point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
   market.digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);
   if(market.point<=0.0 || market.ask<=0.0 || market.bid<=0.0)
      return false;
   market.spread_points=(market.ask-market.bid)/market.point;
   market.atr=atr[0];
   market.atr_points=market.atr/market.point;
   market.ema_fast=fast[1];
   market.ema_fast_prev=fast[0];
   market.ema_slow=slow[0];
   market.rsi=rsi[0];
   market.adx=adx[0];
   market.open=iOpen(_Symbol,InpTimeframe,1);
   market.high=iHigh(_Symbol,InpTimeframe,1);
   market.low=iLow(_Symbol,InpTimeframe,1);
   market.close=iClose(_Symbol,InpTimeframe,1);
   market.previous_high=iHigh(_Symbol,InpTimeframe,2);
   market.previous_low=iLow(_Symbol,InpTimeframe,2);
   market.tick_volume=iVolume(_Symbol,InpTimeframe,1);
   market.average_tick_volume=0.0;
   for(int shift=2;shift<22;shift++)
      market.average_tick_volume+=(double)iVolume(_Symbol,InpTimeframe,shift);
   market.average_tick_volume/=20.0;
   return market.bar_time>0 && market.atr>0.0 && market.close>0.0;
  }

bool IsNewBar(void)
  {
   datetime current=iTime(_Symbol,InpTimeframe,0);
   if(current<=0 || current==g_last_bar_time)
      return false;
   g_last_bar_time=current;
   return true;
  }

bool IsSafeTradingEnvironment(void)
  {
   return MQLInfoInteger(MQL_TESTER) ||
          AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;
  }

void EvaluateEntry(void)
  {
   SMarketSnapshot market;
   if(!ReadSnapshot(market))
     {
      g_logger.Write("WARN",_Symbol,"Engine","Indicator history is not ready; skipped this bar");
      return;
     }

   SAgentVote atlas=g_atlas.Analyze(market,InpUseMarketDepth && g_market_book_subscribed);
   SAgentVote vega=g_vega.Analyze(market,InpMinimumAdx);
   string timing_reason;
   if(!g_titan.IsAllowed(market,InpMaximumSpreadPoints,InpMinimumAtrPoints,
                         InpUseSessionFilter,InpSessionStartHour,InpSessionEndHour,
                         InpRolloverBlockMinutes,timing_reason))
     {
      g_logger.Write("INFO",_Symbol,"Titan",timing_reason);
      return;
     }

   STradeIdea idea=g_orion.Build(market,atlas,vega,InpMinimumConfidence,InpRewardRisk);
   if(!idea.valid)
     {
      g_logger.Write("INFO",_Symbol,"Orion",idea.rationale);
      return;
     }

   if(!InpEnableTrading)
     {
      g_logger.Write("INFO",_Symbol,"Observe",
                     StringFormat("%s idea at %.5f, SL %.5f, TP %.5f, confidence %.2f (orders disabled)",
                                  idea.direction>0 ? "BUY" : "SELL",
                                  idea.entry,idea.stop_loss,idea.take_profit,idea.confidence));
      return;
     }
   if(!IsSafeTradingEnvironment())
     {
      g_logger.Write("ERROR",_Symbol,"Nova",
                     "Order denied: trading is permitted only in Strategy Tester or on a demo account");
      return;
     }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ||
      !MQLInfoInteger(MQL_TRADE_ALLOWED))
     {
      g_logger.Write("WARN",_Symbol,"Engine","Terminal or EA algorithmic trading permission is disabled");
      return;
     }
   long stops_level=SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL);
   double minimum_stop_distance=(double)stops_level*market.point;
   if(MathAbs(idea.entry-idea.stop_loss)<minimum_stop_distance ||
      MathAbs(idea.take_profit-idea.entry)<minimum_stop_distance)
     {
      g_logger.Write("WARN",_Symbol,"Nova",
                     "Order skipped: broker minimum stop distance exceeds the strategy bracket");
      return;
     }

   double volume=0.0;
   double risk_money=0.0;
   string risk_reason;
   if(!g_nova.Approve(market,idea.direction,idea.entry,idea.stop_loss,InpMagicNumber,
                      InpRiskPerTradePct,InpMaximumPortfolioRiskPct,InpMaximumDailyLossPct,
                      InpMaximumOpenPositions,InpMaximumFreeMarginUsePct,
                      volume,risk_money,risk_reason))
     {
      g_logger.Write("WARN",_Symbol,"Nova",risk_reason);
      return;
     }

   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(InpDeviationPoints);
   double stop=NormalizePrice(idea.stop_loss);
   double target=NormalizePrice(idea.take_profit);
   bool sent=(idea.direction>0 ?
              g_trade.Buy(volume,_Symbol,0.0,stop,target,"Calibrain Orion") :
              g_trade.Sell(volume,_Symbol,0.0,stop,target,"Calibrain Orion"));
   if(!sent || (g_trade.ResultRetcode()!=TRADE_RETCODE_DONE &&
                g_trade.ResultRetcode()!=TRADE_RETCODE_PLACED &&
                g_trade.ResultRetcode()!=TRADE_RETCODE_DONE_PARTIAL))
     {
      g_logger.Write("ERROR",_Symbol,"Execution",
                     StringFormat("Order failed, retcode %u: %s",
                                  g_trade.ResultRetcode(),g_trade.ResultRetcodeDescription()));
      return;
     }
   g_logger.Write("INFO",_Symbol,"Execution",
                  StringFormat("%s %.2f lots at %.*f, SL %.*f, TP %.*f, risk %.2f; %s",
                               idea.direction>0 ? "BUY" : "SELL",volume,
                               market.digits,g_trade.ResultPrice(),
                               market.digits,stop,market.digits,target,
                               risk_money,risk_reason));
  }

int OnInit(void)
  {
   g_logger.Init();
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(InpDeviationPoints);
   if(!g_trade.SetTypeFillingBySymbol(_Symbol))
      g_logger.Write("WARN",_Symbol,"Startup","Could not resolve symbol filling mode; order may be rejected");
   g_nova.EnsureDailyBaseline(InpMagicNumber);

   if(InpEnableTrading && !IsSafeTradingEnvironment())
     {
      g_logger.Write("ERROR",_Symbol,"Startup",
                     "Trading mode refused: use Strategy Tester or a demo account");
      return INIT_FAILED;
     }
   if(InpFastEma<=0 || InpSlowEma<=InpFastEma ||
      InpRsiPeriod<=0 || InpAdxPeriod<=0 || InpAtrPeriod<=0 ||
      InpRewardRisk<1.0 || InpRewardRisk>10.0 ||
      InpMinimumAdx<0.0 || InpMinimumAdx>100.0 ||
      InpRiskPerTradePct<=0.0 || InpRiskPerTradePct>2.0 ||
      InpMaximumPortfolioRiskPct<=0.0 || InpMaximumPortfolioRiskPct>10.0 ||
      InpMaximumDailyLossPct<=0.0 || InpMaximumDailyLossPct>20.0 ||
      InpMaximumFreeMarginUsePct<=0.0 || InpMaximumFreeMarginUsePct>50.0 ||
      InpMinimumConfidence<0.0 || InpMinimumConfidence>1.0 ||
      InpMaximumOpenPositions<=0 || InpMaximumOpenPositions>20 ||
      InpMaximumSpreadPoints<=0 || InpMinimumAtrPoints<=0.0 ||
      InpRolloverBlockMinutes<0 || InpRolloverBlockMinutes>60 ||
      InpSessionStartHour<0 || InpSessionStartHour>23 ||
      InpSessionEndHour<0 || InpSessionEndHour>23 ||
      InpBreakEvenAtR<0.0 || InpTrailStartAtR<0.0 ||
      InpTrailAtrMultiple<0.0 || InpBreakEvenOffsetPoints<0.0)
     {
      g_logger.Write("ERROR",_Symbol,"Startup","Invalid input configuration");
      return INIT_PARAMETERS_INCORRECT;
     }

   g_atr_handle=iATR(_Symbol,InpTimeframe,InpAtrPeriod);
   g_fast_ema_handle=iMA(_Symbol,InpTimeframe,InpFastEma,0,MODE_EMA,PRICE_CLOSE);
   g_slow_ema_handle=iMA(_Symbol,InpTimeframe,InpSlowEma,0,MODE_EMA,PRICE_CLOSE);
   g_rsi_handle=iRSI(_Symbol,InpTimeframe,InpRsiPeriod,PRICE_CLOSE);
   g_adx_handle=iADX(_Symbol,InpTimeframe,InpAdxPeriod);
   if(g_atr_handle==INVALID_HANDLE || g_fast_ema_handle==INVALID_HANDLE ||
      g_slow_ema_handle==INVALID_HANDLE || g_rsi_handle==INVALID_HANDLE ||
      g_adx_handle==INVALID_HANDLE)
     {
      g_logger.Write("ERROR",_Symbol,"Startup","Could not create one or more indicator handles");
      return INIT_FAILED;
     }

   if(InpUseMarketDepth)
     {
      g_market_book_subscribed=MarketBookAdd(_Symbol);
      if(!g_market_book_subscribed)
         g_logger.Write("WARN",_Symbol,"Atlas","Market Depth unavailable; tick-volume proxy will be used");
     }
   g_last_bar_time=iTime(_Symbol,InpTimeframe,0);
   g_logger.Write("INFO",_Symbol,"Startup",
                  StringFormat("Ready on %s; timeframe %s; trading %s; demo-only",
                               _Symbol,EnumToString(InpTimeframe),
                               InpEnableTrading ? "enabled" : "observe-only"));
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_atr_handle!=INVALID_HANDLE)
      IndicatorRelease(g_atr_handle);
   if(g_fast_ema_handle!=INVALID_HANDLE)
      IndicatorRelease(g_fast_ema_handle);
   if(g_slow_ema_handle!=INVALID_HANDLE)
      IndicatorRelease(g_slow_ema_handle);
   if(g_rsi_handle!=INVALID_HANDLE)
      IndicatorRelease(g_rsi_handle);
   if(g_adx_handle!=INVALID_HANDLE)
      IndicatorRelease(g_adx_handle);
   if(g_market_book_subscribed)
      MarketBookRelease(_Symbol);
   g_logger.Write("INFO",_Symbol,"Shutdown",StringFormat("EA stopped (reason %d)",reason));
   g_logger.Deinit();
  }

void OnTick(void)
  {
   double atr[1];
   if(g_atr_handle!=INVALID_HANDLE && CopyBuffer(g_atr_handle,0,1,1,atr)==1)
     {
      g_trade.SetExpertMagicNumber(InpMagicNumber);
      g_luna.Manage(g_trade,g_logger,_Symbol,InpMagicNumber,atr[0],
                    InpBreakEvenAtR,InpTrailStartAtR,InpTrailAtrMultiple,
                    InpBreakEvenOffsetPoints,InpRewardRisk);
     }
   if(IsNewBar())
      EvaluateEntry();
  }

void OnTradeTransaction(const MqlTradeTransaction &transaction,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(transaction.type!=TRADE_TRANSACTION_DEAL_ADD || transaction.deal==0 ||
      !HistoryDealSelect(transaction.deal))
      return;
   if((ulong)HistoryDealGetInteger(transaction.deal,DEAL_MAGIC)!=InpMagicNumber)
      return;
   string symbol=HistoryDealGetString(transaction.deal,DEAL_SYMBOL);
   long entry=HistoryDealGetInteger(transaction.deal,DEAL_ENTRY);
   double profit=HistoryDealGetDouble(transaction.deal,DEAL_PROFIT);
   double commission=HistoryDealGetDouble(transaction.deal,DEAL_COMMISSION);
   double swap=HistoryDealGetDouble(transaction.deal,DEAL_SWAP);
   g_logger.Write("INFO",symbol,"Trade",
                  StringFormat("Deal %I64u entry=%d profit=%.2f commission=%.2f swap=%.2f",
                               transaction.deal,entry,profit,commission,swap));
  }
