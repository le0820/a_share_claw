"""Isolated pinned SDK acquisition. No inherited credentials, proxy or global SDK state."""
import json
import sys
from importlib.metadata import version


def main():
    if version("easy-tdx")!="1.20.4":raise ValueError("unsupported_sdk_version")
    from easy_tdx import Adjust, ExMarket, MacClient, MacExClient, Market, Period
    from easy_tdx.mac.enums import BoardType
    p=json.load(sys.stdin)
    if p["operation"]=="quote_snapshot":
        from dataclasses import asdict
        import struct
        from easy_tdx.codec.bitmap import FieldBit,PresetField
        from easy_tdx.mac.commands.board_members_quotes import BoardMembersQuotesCmd
        from easy_tdx.mac.enums import SortType,SortOrder
        class CountedQuotes(BoardMembersQuotesCmd):
            def parse_response(self,body):
                total,rows=struct.unpack_from("<IH",body,20)
                parsed=super().parse_response(body)
                if len(parsed)!=rows:raise ValueError("incomplete_quote_page")
                return total,parsed
        fields=(PresetField.BASIC+FieldBit.AMOUNT+FieldBit.SERVER_UPDATE_DATE+FieldBit.SERVER_UPDATE_TIME+
                FieldBit.MAIN_NET_AMOUNT+FieldBit.MAIN_NET_3D_AMOUNT+FieldBit.MAIN_NET_5D_AMOUNT+FieldBit.INDUSTRY)
        for host in ("121.36.248.138","123.60.47.136"):
            try:
                quotes=[];totals={};page_counts={}
                with MacClient(host=host,port=7709,timeout=8,auto_reconnect=False) as client:
                    for label,category in [("SH",0),("SZ",2),("BJ",12)]:
                        offset=0;expected=None;page_counts[label]=0
                        while expected is None or offset<expected:
                            total,batch=client._execute(CountedQuotes(category,SortType.CODE,offset,80,SortOrder.ASC,fields,[]))
                            if expected is None:expected=total
                            if expected!=total or total>p["max_rows"] or not batch:raise ValueError("incomplete_quote_universe")
                            if offset+len(batch)>expected or len(quotes)+len(batch)>p["max_rows"]:raise ValueError("excess_quote_universe")
                            quotes.extend({'requested_market':label,**asdict(row)} for row in batch)
                            offset+=len(batch);page_counts[label]+=1
                        totals[label]=expected
                    print(json.dumps({'sdk_version':version("easy-tdx"),'endpoint':f'tcp://{host}:7709','market_totals':totals,
                        'page_counts':page_counts,'quotes':quotes},ensure_ascii=False,allow_nan=False));return
            except Exception:continue
        raise RuntimeError("source_unavailable")
    if p["kind"]=="international":
        factory=MacExClient;hosts=("116.205.135.205","121.37.232.167");port=7727
    else:
        factory=MacClient;hosts=("121.36.248.138","123.60.47.136");port=7709
    last=None
    for host in hosts:
        try:
            with factory(host=host,port=port,timeout=8,auto_reconnect=False) as client:
                if p["kind"]=="international":
                    identity=client.goods_list(p.get("catalog_market",ExMarket.INTL_INDEX),start=0,count=600)
                    bars=None if p["operation"] in {"catalog","extended_catalog"} else client.goods_kline(ExMarket.INTL_INDEX,p["code"],Period.DAILY,count=p["count"],adjust=Adjust.NONE)
                else:
                    if p["operation"]=="identity":
                        identity=client.get_symbol_info(p["market"],p["code"]);bars=None
                    elif p["operation"]=="board_catalog":
                        identity=client.get_board_list(BoardType.HY,count=10000);bars=None
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
