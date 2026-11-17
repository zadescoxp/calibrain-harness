#ifndef CALIBRAIN_ATLAS_MQH
#define CALIBRAIN_ATLAS_MQH

#include <Calibrain/Types.mqh>

class CAtlasAgent
  {
public:
   SAgentVote Analyze(const SMarketSnapshot &market,const bool use_market_depth)
     {
      SAgentVote vote;
      vote.direction=0;
      vote.confidence=0.0;
      vote.rationale="No unusual flow";

      if(use_market_depth)
        {
         MqlBookInfo book[];
         if(MarketBookGet(market.symbol,book))
           {
            double bid_depth=0.0;
            double ask_depth=0.0;
            for(int i=0;i<ArraySize(book);i++)
              {
               double quantity=(book[i].volume_real>0.0 ? book[i].volume_real : (double)book[i].volume);
               if(book[i].type==BOOK_TYPE_BUY)
                  bid_depth+=quantity;
               else if(book[i].type==BOOK_TYPE_SELL)
                  ask_depth+=quantity;
              }

            double total=bid_depth+ask_depth;
            if(total>0.0)
              {
               double imbalance=(bid_depth-ask_depth)/total;
               if(imbalance>=0.15)
                 {
                  vote.direction=1;
                  vote.confidence=MathMin(0.85,0.50+MathAbs(imbalance)*0.7);
                  vote.rationale="DOM bid depth leads ask depth";
                  return vote;
                 }
               if(imbalance<=-0.15)
                 {
                  vote.direction=-1;
                  vote.confidence=MathMin(0.85,0.50+MathAbs(imbalance)*0.7);
                  vote.rationale="DOM ask depth leads bid depth";
                  return vote;
                 }
              }
           }
        }

      if(market.average_tick_volume<=0.0 ||
         market.tick_volume<market.average_tick_volume*1.5 ||
         market.close==market.open)
        {
         vote.rationale="No volume anomaly; tick volume is a broker-specific proxy";
         return vote;
        }

      vote.direction=(market.close>market.open ? 1 : -1);
      double relative_volume=(double)market.tick_volume/market.average_tick_volume;
      vote.confidence=MathMin(0.75,0.45+0.10*relative_volume);
      vote.rationale="Unusual tick volume confirms candle direction; not institutional flow";
      return vote;
     }
  };

#endif
