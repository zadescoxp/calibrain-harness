#ifndef CALIBRAIN_TYPES_MQH
#define CALIBRAIN_TYPES_MQH

struct SMarketSnapshot
  {
   string            symbol;
   ENUM_TIMEFRAMES   timeframe;
   datetime          bar_time;
   double            bid;
   double            ask;
   double            point;
   int               digits;
   double            spread_points;
   double            atr;
   double            atr_points;
   double            ema_fast;
   double            ema_fast_prev;
   double            ema_slow;
   double            rsi;
   double            adx;
   double            open;
   double            high;
   double            low;
   double            close;
   double            previous_high;
   double            previous_low;
   long              tick_volume;
   double            average_tick_volume;
  };

struct SAgentVote
  {
   int               direction;
   double            confidence;
   string            rationale;
  };

struct STradeIdea
  {
   bool              valid;
   int               direction;
   double            confidence;
   double            entry;
   double            stop_loss;
   double            take_profit;
   string            rationale;
  };

#endif
