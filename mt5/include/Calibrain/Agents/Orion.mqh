#ifndef CALIBRAIN_ORION_MQH
#define CALIBRAIN_ORION_MQH

#include <Calibrain/Types.mqh>

class COrionAgent
  {
public:
   STradeIdea Build(const SMarketSnapshot &market,
                    const SAgentVote &atlas,
                    const SAgentVote &vega,
                    const double minimum_confidence,
                    const double reward_risk)
     {
      STradeIdea idea;
      idea.valid=false;
      idea.direction=0;
      idea.confidence=0.0;
      idea.entry=0.0;
      idea.stop_loss=0.0;
      idea.take_profit=0.0;
      idea.rationale="No entry: trend breakout not confirmed";

      if(market.atr<=0.0 || market.close<=0.0 || reward_risk<1.0)
         return idea;

      bool bullish=(market.ema_fast>market.ema_slow &&
                    market.ema_fast>market.ema_fast_prev &&
                    market.close>market.previous_high &&
                    market.rsi>=52.0 && market.rsi<=68.0);
      bool bearish=(market.ema_fast<market.ema_slow &&
                    market.ema_fast<market.ema_fast_prev &&
                    market.close<market.previous_low &&
                    market.rsi<=48.0 && market.rsi>=32.0);
      int direction=(bullish ? 1 : (bearish ? -1 : 0));
      if(direction==0)
         return idea;

      if(vega.direction!=direction)
        {
         idea.rationale="Breakout rejected: Vega does not confirm direction";
         return idea;
        }

      double confidence=0.55+0.25*vega.confidence;
      if(atlas.direction==direction)
         confidence+=0.12*atlas.confidence;
      else if(atlas.direction==-direction)
         confidence-=0.10*atlas.confidence;
      confidence=MathMax(0.0,MathMin(0.95,confidence));
      if(confidence<minimum_confidence)
        {
         idea.rationale="Consensus confidence is below the entry threshold";
         return idea;
        }

      double risk_distance=market.atr*1.5;
      idea.valid=true;
      idea.direction=direction;
      idea.confidence=confidence;
      idea.entry=(direction>0 ? market.ask : market.bid);
      idea.stop_loss=(direction>0 ? idea.entry-risk_distance : idea.entry+risk_distance);
      idea.take_profit=(direction>0 ? idea.entry+risk_distance*reward_risk :
                                        idea.entry-risk_distance*reward_risk);
      idea.rationale=StringFormat("%s breakout; Vega confirmed, Atlas confidence %.2f",
                                 (direction>0 ? "Bullish" : "Bearish"),atlas.confidence);
      return idea;
     }
  };

#endif
