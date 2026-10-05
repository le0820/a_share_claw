"""Isolated pinned SDK acquisition. No inherited credentials, proxy or global SDK state."""
import json
import sys
from importlib.metadata import version


def scan_extended_catalog(read_count,read_page,market=62):
    """Native market-sorted directory boundaries plus every row in the selected interval."""
    total=read_count()
    if type(total) is not int or not 0<total<=250000:raise ValueError('invalid_catalog_total:'+str(total))
    probes={}
    def probe(index):
        if index not in probes:
            page=read_page(index,1)
            if not isinstance(page,list) or len(page)!=1:raise ValueError('incomplete_catalog_probe')
            probes[index]=page[0]
        return probes[index]['market']
    def boundary(target):
        low,high=0,total
        while low<high:
            mid=(low+high)//2
            if probe(mid)<target:low=mid+1
            else:high=mid
        return low
    start,end=boundary(market),boundary(market+1)
    if not 0<end-start<=10000:raise ValueError('invalid_market_catalog_total')
    if (probe(start)!=market or probe(end-1)!=market or
        start>0 and probe(start-1)>=market or end<total and probe(end)<=market):raise ValueError('invalid_catalog_boundary')
    ordered=sorted(probes.items())
    if any(a[1]['market']>b[1]['market'] for a,b in zip(ordered,ordered[1:])):raise ValueError('unordered_catalog_probes')
    rows=[];seen={};pages=0
    for offset in range(start,end,1000):
        count=min(1000,end-offset);batch=read_page(offset,count)
        if not isinstance(batch,list) or len(batch)!=count:raise ValueError('incomplete_catalog_page')
        for index,row in enumerate(batch):
            key=(row['market'],row['code'])
            if row['market']!=market:raise ValueError('wrong_catalog_market')
            seen[key]=seen.get(key,0)+1;rows.append({**row,'native_offset':offset+index})
        pages+=1
    if read_count()!=total:raise ValueError('catalog_total_changed')
    return {'native_total':total,'market_start':start,'market_end':end,'market_total':end-start,
        'page_count':pages,'coverage_status':'complete_against_native_market_boundaries',
        'boundary_probes':[{'offset':i,'row':r} for i,r in ordered],
        'ambiguous_identities':[{'market':m,'code':c,'records':n} for (m,c),n in seen.items() if n>1],'rows':rows}


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
                    categories={'SH':0,'SZ':2,'BJ':12}
                    for label in p.get('markets',['SH','SZ','BJ']):
                        category=categories[label]
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
                    if p['operation']=='extended_catalog':
                        import struct
                        from dataclasses import asdict
                        from easy_tdx.ex.commands.get_instrument_count import GetExInstrumentCountCmd
                        from easy_tdx.ex.commands.get_instrument_info import GetExInstrumentInfoCmd
                        class CheckedPage(GetExInstrumentInfoCmd):
                            def parse_response(self,body):
                                if len(body)<6:raise ValueError('incomplete_catalog_page')
                                start,count=struct.unpack_from('<IH',body,0)
                                parsed=super().parse_response(body)
                                if start!=self.start or count>self.count or len(parsed)!=count or len(body)!=6+64*count:raise ValueError('incomplete_catalog_page')
                                return parsed
                        def page(start,count):
                            result=[]
                            for row in client._execute(CheckedPage(start,count)):
                                d=asdict(row);d['raw_hex']=d.pop('_raw').hex();result.append(d)
                            return result
                        catalog=scan_extended_catalog(lambda:client._execute(GetExInstrumentCountCmd()),page,p['catalog_market'])
                        target=[{k:v for k,v in row.items() if k!='raw_hex'} for row in catalog['rows'] if row['market']==p['catalog_market']]
                        print(json.dumps({'sdk_version':version('easy-tdx'),'endpoint':f'tcp://{host}:{port}',
                            'identity':target,'bars':[],'native_catalog':catalog},ensure_ascii=False,allow_nan=False));return
                    identity=client.goods_list(p.get("catalog_market",ExMarket.INTL_INDEX),start=0,count=600)
                    bars=None if p["operation"] in {"catalog","extended_catalog"} else client.goods_kline(p.get("market",ExMarket.INTL_INDEX),p["code"],Period.DAILY,count=p["count"],adjust=Adjust.NONE)
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
