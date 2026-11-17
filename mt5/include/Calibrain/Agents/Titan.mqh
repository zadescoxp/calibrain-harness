#ifndef CALIBRAIN_TITAN_MQH
#define CALIBRAIN_TITAN_MQH

#include <Calibrain/Types.mqh>

class CTitanAgent
  {
public:
   bool IsAllowed(const SMarketSnapshot &market,
                  const double maximum_spread_points,
                  const double minimum_atr_points,
                  const bool use_session_filter,
                  const int session_start_hour,
                  const int session_end_hour,
                  const int rollover_block_minutes,
                  string &reason)
     {
      reason="";
      if(market.spread_points>maximum_spread_points)
        {
         reason="Spread exceeds the configured maximum";
         return false;
        }
      if(market.atr_points<minimum_atr_points)
        {
         reason="Volatility is below the configured minimum";
         return false;
        }

      MqlDateTime now;
      TimeToStruct(TimeCurrent(),now);
      int minute_of_day=now.hour*60+now.min;
      if(rollover_block_minutes>0 &&
         (minute_of_day<rollover_block_minutes ||
          minute_of_day>=1440-rollover_block_minutes))
        {
         reason="Blocked around broker-server midnight";
         return false;
        }

      if(use_session_filter)
        {
         bool in_session;
         if(session_start_hour<=session_end_hour)
            in_session=(now.hour>=session_start_hour && now.hour<session_end_hour);
         else
            in_session=(now.hour>=session_start_hour || now.hour<session_end_hour);
         if(!in_session)
           {
            reason="Outside the configured broker-server session";
            return false;
           }
        }

      reason="Timing, spread and volatility gates passed";
      return true;
     }
  };

#endif
