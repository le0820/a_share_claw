"""Isolated pinned SDK acquisition. No inherited credentials, proxy or global SDK state."""
import json
import sys
from importlib.metadata import version


def main():
    if version("easy-tdx")!="1.20.4":raise ValueError("unsupported_sdk_version")
    from easy_tdx import Adjust, ExMarket, MacClient, MacExClient, Market, Period
    p=json.load(sys.stdin)
    if p["kind"]=="international":
        factory=MacExClient;hosts=("116.205.135.205","121.37.232.167");port=7727
    else:
        factory=MacClient;hosts=("121.36.248.138","123.60.47.136");port=7709
    last=None
    for host in hosts:
        try:
            with factory(host=host,port=port,timeout=8,auto_reconnect=False) as client:
                if p["kind"]=="international":
                    identity=client.goods_list(ExMarket.INTL_INDEX,start=0,count=600)
                    bars=None if p["operation"]=="catalog" else client.goods_kline(ExMarket.INTL_INDEX,p["code"],Period.DAILY,count=p["count"],adjust=Adjust.NONE)
                else:
                    market=Market.SH if p["market"]==1 else Market.SZ
                    identity=client.get_symbol_info(market,p["code"])
                    bars=client.get_stock_kline(market,p["code"],Period.DAILY,count=p["count"],adjust=Adjust.NONE)
                result={"sdk_version":version("easy-tdx"),"endpoint":f"tcp://{host}:{port}",
                    "identity":json.loads(identity.to_json(orient="records",date_format="iso")),
                    "bars":[] if bars is None else json.loads(bars.to_json(orient="records",date_format="iso"))}
                print(json.dumps(result,ensure_ascii=False,allow_nan=False));return
        except Exception as exc:last=exc
    raise RuntimeError("source_unavailable") from last


if __name__=="__main__":main()
