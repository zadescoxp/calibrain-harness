#ifndef CALIBRAIN_VEGA_MQH
#define CALIBRAIN_VEGA_MQH

#include <Calibrain/Types.mqh>

class CVegaAgent
  {
public:
   SAgentVote Analyze(const SMarketSnapshot &market,const double minimum_adx)
     {
      SAgentVote vote;
      vote.direction=0;
      vote.confidence=0.0;
      vote.rationale="Market balance is mixed or weak";

      if(market.adx<minimum_adx)
        {
         vote.rationale="Trend strength is below the configured ADX floor";
         return vote;
        }

      bool bullish=(market.ema_fast>market.ema_slow &&
                    market.ema_fast>market.ema_fast_prev &&
                    market.rsi>=52.0 && market.rsi<=68.0);
      bool bearish=(market.ema_fast<market.ema_slow &&
                    market.ema_fast<market.ema_fast_prev &&
                    market.rsi<=48.0 && market.rsi>=32.0);
      if(bullish)
        {
         vote.direction=1;
         vote.confidence=MathMin(0.90,0.50+market.adx/100.0);
         vote.rationale="Bullish trend and RSI balance";
        }
      else if(bearish)
        {
         vote.direction=-1;
         vote.confidence=MathMin(0.90,0.50+market.adx/100.0);
         vote.rationale="Bearish trend and RSI balance";
        }
      return vote;
     }
  };

#endif
