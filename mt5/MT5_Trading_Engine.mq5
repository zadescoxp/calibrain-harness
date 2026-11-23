#property strict
#property description "Multi-agent MT5 trading engine with Atlas, Vega, Orion, Titan, Luna and Nova"
#property version "1.00"
#property script_show_inputs

#include <Trade/Trade.mqh>

input bool EnableLiveTrading = false;
input bool TradeLongs = true;
input bool TradeShorts = true;
input double RiskPercent = 1.0;
input double MaxDailyLossPercent = 3.0;
input double MinSignalScore = 0.58;
input int FastMA = 20;
input int SlowMA = 50;
input int RSI_Period = 14;
input int ATR_Period = 14;
input int TrailStopPips = 35;
input int MaxPositions = 2;
input int SessionStartHour = 8;
input int SessionEndHour = 18;

CTrade g_trade;

enum ENUM_SIGNAL_DIRECTION
{
   SIGNAL_NONE = 0,
   SIGNAL_BUY = 1,
   SIGNAL_SELL = -1
};

struct MarketContext
{
   double bid;
   double ask;
   double spread;
   double price;
   double fastMA;
   double slowMA;
   double rsi;
   double atr;
   double avgVolume;
   double currentVolume;
   double signalBias;
   double sessionScore;
};

struct Signal
{
   ENUM_SIGNAL_DIRECTION direction;
   double score;
   double entry;
   double stopLoss;
   double takeProfit;
   double lotSize;
   string reason;
};

class CAtlasAgent
{
public:
   double Evaluate(const MarketContext &ctx)
   {
      double volumeRatio = 0.0;
      if(ctx.avgVolume > 0.0)
         volumeRatio = (ctx.currentVolume - ctx.avgVolume) / (ctx.avgVolume + 1e-6);

      double marketPressure = MathMax(-1.0, MathMin(1.0, volumeRatio * 0.8));
      double spreadPressure = (ctx.spread <= 0.0008) ? 0.25 : -0.25;
      double pressure = marketPressure + spreadPressure;
      return MathMax(-1.0, MathMin(1.0, pressure));
   }
};

class CVegaAgent
{
public:
   double Evaluate(const MarketContext &ctx)
   {
      double trendBias = 0.0;
      if(ctx.fastMA > ctx.slowMA)
         trendBias = 0.7;
      else if(ctx.fastMA < ctx.slowMA)
         trendBias = -0.7;

      double momentumBias = 0.0;
      if(ctx.rsi > 55.0 && ctx.rsi < 75.0)
         momentumBias = 0.5;
      else if(ctx.rsi < 45.0 && ctx.rsi > 25.0)
         momentumBias = -0.5;

      double combined = trendBias + momentumBias;
      return MathMax(-1.0, MathMin(1.0, combined * 0.75));
   }
};

class COrionAgent
{
public:
   double Evaluate(const MarketContext &ctx)
   {
      double confidence = 0.0;
      if(ctx.price > ctx.fastMA)
         confidence += 0.35;
      else
         confidence -= 0.35;

      if(ctx.price > ctx.slowMA)
         confidence += 0.25;
      else
         confidence -= 0.25;

      if(ctx.rsi > 52.0 && ctx.rsi < 72.0)
         confidence += 0.20;
      else if(ctx.rsi > 28.0 && ctx.rsi < 48.0)
         confidence -= 0.20;

      if(ctx.atr > 0.0)
         confidence += 0.15 * MathMin(1.0, ctx.atr / (ctx.price * 0.01 + 1e-6));

      return MathMax(-1.0, MathMin(1.0, confidence));
   }
};

class CTitanAgent
{
public:
   double Evaluate(const MarketContext &ctx)
   {
      MqlDateTime now;
      TimeToStruct(TimeLocal(), now);
      int hour = now.hour;

      double sessionScore = 0.0;
      if(hour >= SessionStartHour && hour <= SessionEndHour)
         sessionScore = 0.25;
      else
         sessionScore = -0.25;

      ctx.sessionScore = sessionScore;
      return sessionScore;
   }
};

class CLunaAgent
{
public:
   void AdjustPositionTrailingStop(const long ticket, const double entryPrice, const double direction)
   {
      if(ticket <= 0)
         return;

      if(!PositionSelectByTicket(ticket))
         return;

      double currentPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
      double currentStop = PositionGetDouble(POSITION_SL);
      double newStop = currentStop;

      if(direction > 0)
      {
         double trailingTarget = entryPrice + TrailStopPips * _Point * 10.0;
         if(currentPrice > trailingTarget)
         {
            newStop = MathMax(currentStop, trailingTarget - 10.0 * _Point);
         }
      }
      else if(direction < 0)
      {
         double trailingTarget = entryPrice - TrailStopPips * _Point * 10.0;
         if(currentPrice < trailingTarget)
         {
            newStop = MathMin(currentStop, trailingTarget + 10.0 * _Point);
         }
      }

      if(newStop > 0.0 && MathAbs(newStop - currentStop) > 0.0)
      {
         g_trade.PositionModify(ticket, newStop, PositionGetDouble(POSITION_TP));
      }
   }
};

class CNovaAgent
{
public:
   bool ValidateRisk(const double entryPrice, const double stopLoss, const ENUM_SIGNAL_DIRECTION direction)
   {
      double riskPercent = RiskPercent;
      double maxDailyLoss = AccountInfoDouble(ACCOUNT_BALANCE) * (MaxDailyLossPercent / 100.0);
      double dailyProfit = AccountInfoDouble(ACCOUNT_PROFIT);

      if(dailyProfit < -maxDailyLoss)
         return false;

      double distance = MathAbs(entryPrice - stopLoss);
      if(distance <= 0.0)
         return false;

      double riskValue = AccountInfoDouble(ACCOUNT_BALANCE) * (riskPercent / 100.0);
      double tickValue = MarketInfo(_Symbol, MODE_TICKVALUE);
      double estimatedLot = riskValue / MathMax(0.0001, distance * tickValue / 100000.0);
      if(estimatedLot <= 0.0)
         return false;

      return true;
   }

   double CalculateLotSize(const double entryPrice, const double stopLoss)
   {
      double riskValue = AccountInfoDouble(ACCOUNT_BALANCE) * (RiskPercent / 100.0);
      double stopDistance = MathAbs(entryPrice - stopLoss);
      double tickValue = MathMax(0.0001, MarketInfo(_Symbol, MODE_TICKVALUE));
      double estimatedLot = riskValue / MathMax(0.0001, stopDistance * tickValue / 100000.0);
      return MathMax(0.01, MathMin(10.0, estimatedLot));
   }
};

class CStrategyEngine
{
private:
   CAtlasAgent m_atlas;
   CVegaAgent m_vega;
   COrionAgent m_orion;
   CTitanAgent m_titan;
   CNovaAgent m_nova;
   CLunaAgent m_luna;

public:
   Signal BuildSignal(const MarketContext &ctx)
   {
      Signal result;
      result.direction = SIGNAL_NONE;
      result.score = 0.0;
      result.entry = ctx.price;
      result.stopLoss = ctx.price;
      result.takeProfit = ctx.price;
      result.lotSize = 0.0;
      result.reason = "neutral";

      double atlas = m_atlas.Evaluate(ctx);
      double vega = m_vega.Evaluate(ctx);
      double orion = m_orion.Evaluate(ctx);
      double titan = m_titan.Evaluate(ctx);

      double combined = (atlas * 0.32) + (vega * 0.30) + (orion * 0.24) + (titan * 0.14);
      double absScore = MathAbs(combined);

      if(absScore < MinSignalScore)
      {
         result.score = absScore;
         return result;
      }

      if(combined > 0.0)
      {
         result.direction = SIGNAL_BUY;
         result.score = absScore;
         result.reason = "bullish alignment";
      }
      else
      {
         result.direction = SIGNAL_SELL;
         result.score = absScore;
         result.reason = "bearish alignment";
      }

      double entryBuffer = MathMax(10.0 * _Point, ctx.atr * 0.35);
      double stopDistance = MathMax(20.0 * _Point, ctx.atr * 0.7);
      double riskMultiplier = 1.5;

      if(result.direction == SIGNAL_BUY)
      {
         result.entry = ctx.ask;
         result.stopLoss = result.entry - stopDistance;
         result.takeProfit = result.entry + (stopDistance * riskMultiplier * 2.1);
      }
      else if(result.direction == SIGNAL_SELL)
      {
         result.entry = ctx.bid;
         result.stopLoss = result.entry + stopDistance;
         result.takeProfit = result.entry - (stopDistance * riskMultiplier * 2.1);
      }

      if(!m_nova.ValidateRisk(result.entry, result.stopLoss, result.direction))
      {
         result.direction = SIGNAL_NONE;
         result.reason = "risk guard";
         result.score = 0.0;
      }
      else
      {
         result.lotSize = m_nova.CalculateLotSize(result.entry, result.stopLoss);
      }

      return result;
   }

   void ManageOpenPositions()
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0)
            continue;

         if(!PositionSelectByTicket(ticket))
            continue;

         double entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
         double currentPrice = PositionGetDouble(POSITION_PRICE_CURRENT);
         int type = (int)PositionGetInteger(POSITION_TYPE);
         double direction = (type == POSITION_TYPE_BUY) ? 1.0 : -1.0;

         double newStop = 0.0;
         if(direction > 0.0)
         {
            newStop = entryPrice + TrailStopPips * _Point * 10.0;
            if(currentPrice > newStop)
               g_trade.PositionModify((long)ticket, newStop, PositionGetDouble(POSITION_TP));
         }
         else if(direction < 0.0)
         {
            newStop = entryPrice - TrailStopPips * _Point * 10.0;
            if(currentPrice < newStop)
               g_trade.PositionModify((long)ticket, newStop, PositionGetDouble(POSITION_TP));
         }
      }
   }
};

MarketContext GetMarketContext()
{
   MarketContext ctx;
   ctx.bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   ctx.ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   ctx.spread = MathAbs(ctx.ask - ctx.bid);
   ctx.price = (ctx.bid + ctx.ask) / 2.0;
   ctx.fastMA = iMA(_Symbol, PERIOD_CURRENT, FastMA, 0, MODE_EMA, PRICE_CLOSE, 0);
   ctx.slowMA = iMA(_Symbol, PERIOD_CURRENT, SlowMA, 0, MODE_SMA, PRICE_CLOSE, 0);
   ctx.rsi = iRSI(_Symbol, PERIOD_CURRENT, RSI_Period, PRICE_CLOSE, 0);
   ctx.atr = iATR(_Symbol, PERIOD_CURRENT, ATR_Period, 0);

   double volumeSum = 0.0;
   for(int i = 0; i < FastMA; i++)
      volumeSum += (double)iVolume(_Symbol, PERIOD_CURRENT, i);
   ctx.avgVolume = (FastMA > 0) ? volumeSum / FastMA : 1.0;
   ctx.currentVolume = (double)iVolume(_Symbol, PERIOD_CURRENT, 0);
   ctx.signalBias = 0.0;
   ctx.sessionScore = 0.0;
   return ctx;
}

bool IsNewBar()
{
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   bool result = (currentBarTime != lastBarTime);
   lastBarTime = currentBarTime;
   return result;
}

bool CanOpenPosition()
{
   if(PositionsTotal() >= MaxPositions)
      return false;

   MqlDateTime now;
   TimeToStruct(TimeLocal(), now);
   return (now.hour >= SessionStartHour && now.hour <= SessionEndHour) || (SessionStartHour == 0 && SessionEndHour == 0);
}

void OnInit()
{
   EventSetTimer(1);
   Print("MT5 Trading Engine initialized. Live trading is disabled by default.");
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   if(!EnableLiveTrading)
      return;

   if(!IsNewBar())
      return;

   if(!CanOpenPosition())
      return;

   MarketContext ctx = GetMarketContext();
   CStrategyEngine engine;
   Signal signal = engine.BuildSignal(ctx);

   if(signal.direction == SIGNAL_BUY && TradeLongs)
   {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = signal.stopLoss;
      double tp = signal.takeProfit;
      g_trade.Buy(signal.lotSize, _Symbol, ask, sl, tp, "ATLAS-BUY");
      Print("BUY signal: ", signal.score, " | reason: ", signal.reason);
   }
   else if(signal.direction == SIGNAL_SELL && TradeShorts)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = signal.stopLoss;
      double tp = signal.takeProfit;
      g_trade.Sell(signal.lotSize, _Symbol, bid, sl, tp, "ATLAS-SELL");
      Print("SELL signal: ", signal.score, " | reason: ", signal.reason);
   }
   else
   {
      Print("No trade: ", signal.reason, " score=", signal.score);
   }

   engine.ManageOpenPositions();
}

void OnTick()
{
   if(!EnableLiveTrading)
      return;

   if(!IsNewBar())
      return;

   MarketContext ctx = GetMarketContext();
   CStrategyEngine engine;
   Signal signal = engine.BuildSignal(ctx);

   if(signal.direction == SIGNAL_BUY && TradeLongs)
   {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      g_trade.Buy(signal.lotSize, _Symbol, ask, signal.stopLoss, signal.takeProfit, "ATLAS-BUY");
   }
   else if(signal.direction == SIGNAL_SELL && TradeShorts)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      g_trade.Sell(signal.lotSize, _Symbol, bid, signal.stopLoss, signal.takeProfit, "ATLAS-SELL");
   }

   engine.ManageOpenPositions();
}
