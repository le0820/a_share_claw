"""Current captured index levels from the user-authorized primary market provider."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import DataError, Manifest, Payload, Provider, Requirement, Transport, iso_date

# Exact index identities; ETFs and distinct index families cannot be substituted.
INDEXES={
    "SPX.SP500":{"name":"S&P 500","market":12,"code":"A_SPX","kind":"international","currency":"USD","unit":"index_points","market_timezone":"America/New_York","native_names":["标普500","S&P 500"]},
    "NDX.NASDAQ":{"name":"NASDAQ 100","market":12,"code":"A_NDX","kind":"international","currency":"USD","unit":"index_points","market_timezone":"America/New_York","native_names":["纳斯达克100","NASDAQ 100"]},
    "000300.SH":{"name":"沪深300","market":1,"code":"000300","kind":"china","currency":"CNY","unit":"index_points","market_timezone":"Asia/Shanghai","native_names":["沪深300","沪深300指数"]},
    "399006.SZ":{"name":"创业板指","market":0,"code":"399006","kind":"china","currency":"CNY","unit":"index_points", "market_timezone":"Asia/Shanghai","native_names":["创业板指","创业板指数"]},
    "000688.SH":{"name":"科创50","market":1,"code":"000688","kind":"china","currency":"CNY","unit":"index_points", "market_timezone":"Asia/Shanghai","native_names":["科创50","科创50指数"]},
    "COMP.NASDAQ":{"name":"NASDAQ Composite","market":12,"code":None,"kind":"international","currency":"USD","unit":"index_points", "market_timezone":"America/New_York","native_names":["NASDAQ Composite","NASDAQ COMPOSITE","纳斯达克综合指数","纳斯达克综合"]},
}
HOSTS=("121.36.248.138","123.60.47.136","116.205.135.205","121.37.232.167")


class WorkerTransport:
    def request(self,params):
        # Import only inside a child after setting its own SDK configuration root.
        with tempfile.TemporaryDirectory(prefix="asclaw-tdx-") as folder:
            env={k:v for k,v in os.environ.items() if k in {"PATH","LANG","LC_ALL","SYSTEMROOT","SYSTEMDRIVE","HOME","USERPROFILE","HOMEDRIVE","HOMEPATH"}}
            env["EASY_TDX_CONFIG_DIR"]=folder
            try:
                result=subprocess.run([sys.executable,"-I",str(Path(__file__).with_name("_tdx_worker.py"))],
                    input=json.dumps(params),capture_output=True,text=True,timeout=60,env=env)
            except subprocess.TimeoutExpired:
                raise DataError("timeout","Index SDK acquisition exceeded 60 seconds",True) from None
            if result.returncode:
                if "PackageNotFoundError" in result.stderr or "ModuleNotFoundError" in result.stderr:
                    raise DataError("not_configured","Install easy-tdx==1.20.4 in the trusted host interpreter")
                if "unsupported_sdk_version" in result.stderr:
                    raise DataError("unsupported_sdk_version","Market adapter requires easy-tdx 1.20.4")
                raise DataError("source_unavailable","Declared index feed is unavailable; no other provider fallback",True)
            if len(result.stdout.encode())>20*1024*1024:
                raise DataError("response_too_large","Decoded SDK snapshot exceeds 20 MB")
            return result.stdout.encode()


def checked_params(r):
    if (not isinstance(r.params,dict) and not hasattr(r.params,"items")):
        raise DataError("invalid_request","Index parameters must be explicit")
    p=dict(r.params)
    if r.capability=="market.a_share_quote_snapshot":
        if p!={"markets":["SH","SZ","BJ"],"max_rows":10000}:
            raise DataError("invalid_request","Freeze complete SH/SZ/BJ quote coverage; no sampled whole-market claim")
        return {"operation":"quote_snapshot","kind":"china","max_rows":10000}
    if r.capability=="market.instrument_identity":
        if set(p)!={"market","code"} or type(p["market"]) is not int or p["market"] not in {0,1,2} or not isinstance(p["code"],str) or not re.fullmatch(r"[0-9]{6}",p["code"]):
            raise DataError("invalid_request","Native identity lookup requires exact domestic market and code; discovery only")
        return {"operation":"identity","kind":"china",**p}
    if r.capability=="market.board_catalog":
        if p!={"classification":"native_industry_level1"}:
            raise DataError("invalid_request","Native board catalog is discovery only, not certified SW classification")
        return {"operation":"board_catalog","kind":"china"}
    if r.capability=="market.extended_index_catalog":
        if set(p)!={"market"} or type(p["market"]) is not int or p["market"] not in {62,70}:
            raise DataError("invalid_request","Only declared native index catalog markets 62/70 are supported")
        return {"operation":"extended_catalog","kind":"international","catalog_market":p["market"]}
    if r.capability=="market.index_catalog":
        if p:raise DataError("invalid_request","Catalog has no free-form provider parameters")
        return {"operation":"catalog","kind":"international"}
    if set(p)!={"symbol","provider_code","start_date","end_date","count"}:
        raise DataError("invalid_request","Index history requires symbol/code/window/count")
    if not isinstance(p["symbol"],str) or p["symbol"] not in INDEXES:
        raise DataError("unsupported_instrument","Use the exact declared index identity, never ETFs or other index proxies")
    spec=INDEXES[p["symbol"]]
    if (not isinstance(p["provider_code"],str) or not re.fullmatch(r"[A-Za-z0-9._#-]{1,32}",p["provider_code"]) or
            spec["code"] is not None and p["provider_code"]!=spec["code"]):
        raise DataError("source_mismatch","Pin the exact native index code; international identity needs explicit catalog review")
    start,end=iso_date(p["start_date"]),iso_date(p["end_date"])
    if not start<=end<=r.as_of_date or type(p["count"]) is not int or not 1<=p["count"]<=600:
        raise DataError("invalid_request","Use a nonfuture window and bounded daily count 1..600")
    return {"operation":"bars","kind":spec["kind"],"market":spec["market"],"code":p["provider_code"],"count":p["count"]}


class EasyTDX(Provider):
    manifest=Manifest("easytdx",("market.index_catalog","market.index_daily_snapshot","market.board_catalog","market.extended_index_catalog","market.instrument_identity","market.a_share_quote_snapshot"),HOSTS,version="1.2.0")
    def __init__(self,settings,transport=None):
        self._settings=dict(settings)
        self.transport=WorkerTransport() if transport is None or isinstance(transport,Transport) else transport

    def fetch(self,r:Requirement):
        request=checked_params(r)
        if r.as_of_date!=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat():
            raise DataError("historical_unavailable","Current index snapshots cannot backdate their capture")
        raw=self.transport.request(request);obj=json.loads(raw)
        if obj.get("sdk_version")!="1.20.4" or obj.get("endpoint") not in {f"tcp://{host}:{7727 if host in HOSTS[2:] else 7709}" for host in HOSTS}:
            raise DataError("source_mismatch","Index response must identify the pinned SDK and declared feed endpoint")
        if r.capability=="market.a_share_quote_snapshot":
            totals=obj.get("market_totals");quotes=obj.get("quotes")
            if not isinstance(totals,dict) or set(totals)!={"SH","SZ","BJ"} or any(type(v) is not int or not 0<v<=10000 for v in totals.values()) or not isinstance(quotes,list):
                raise DataError("insufficient_coverage","Each exchange needs an explicit positive native universe count")
            seen=set();counts={k:0 for k in totals};missing=[]
            for row in quotes:
                label=row.get("requested_market");code=row.get("code");fields=row.get("fields")
                if label not in counts or row.get("market")!={"SH":1,"SZ":0,"BJ":2}[label] or not isinstance(code,str) or not re.fullmatch(r"[0-9]{6}",code) or not isinstance(fields,dict):
                    raise DataError("source_mismatch","Quote identity disagrees with the frozen exchange universe")
                key=(label,code)
                if key in seen:raise DataError("source_disagreement","Duplicate quote identity cannot fill universe coverage")
                seen.add(key);counts[label]+=1
                for key in ("amount","main_net_amount","server_update_date","server_update_time"):
                    value=fields.get(key)
                    if value is None:missing.append({'market':label,'code':code,'field':key})
                    elif type(value) not in (int,float) or not math.isfinite(value):raise DataError("invalid_schema","Quote fields must retain finite native values")
            if counts!=totals:raise DataError("insufficient_coverage","Returned unique quote count must equal every exchange's native total")
            return Payload({"quotes":quotes,"market_totals":totals,"page_counts":obj.get("page_counts"),"coverage_status":"complete_against_native_totals",
                "native_units":{"amount":"CNY","main_net_amount":"CNY"},"missing_fields":missing,"snapshot_as_of_date":r.as_of_date,
                "main_net_definition":"provider proprietary main-order net amount; thresholds not certified","historical_vintage_certified":False},raw,obj["endpoint"],"unverified",
                ["Native quote snapshot discovery only; core still needs observation-date, frozen universe and classification admission",
                 "Missing fields are gaps, never zero; main-order estimates are not total investor cash entering the equity market"])
        rows=obj.get("identity")
        if not isinstance(rows,list) or not rows:
            raise DataError("missing_identity","No native instrument identity was returned")
        if request["kind"]=="international" and len(rows)>=600:
            raise DataError("insufficient_coverage","International catalog may be truncated; do not certify identity from an incomplete page")
        if r.capability in {"market.index_catalog","market.board_catalog","market.extended_index_catalog","market.instrument_identity"}:
            return Payload({"instruments":rows,"format":"native_sdk_instrument_catalog","historical_vintage_certified":False},raw,obj["endpoint"],"unverified",
                ["Native catalog discovery only; canonical identity still needs explicit review before an index requirement"])
        spec=INDEXES[r.params["symbol"]]
        matches=[row for row in rows if row.get("market")==spec["market"] and row.get("code")==request["code"]]
        if len(matches)!=1 or matches[0].get("name") not in spec["native_names"]:
            raise DataError("source_mismatch","Native index name/market/code do not match the fixed canonical identity")
        bars=obj.get("bars")
        if not isinstance(bars,list) or not bars:raise DataError("no_results","No index daily levels returned")
        if len(bars)>r.params["count"]:raise DataError("invalid_schema","SDK returned more rows than requested")
        selected=[];seen=set()
        for row in bars:
            if not isinstance(row,dict):raise DataError("invalid_schema","Native bar must be an object")
            day=iso_date(row["datetime"][:10]) if isinstance(row.get("datetime"),str) else iso_date(row.get("trade_date"))
            if not r.params["start_date"]<=day<=r.params["end_date"]:
                continue  # Raw archive retains unrelated SDK history; it is not an admitted observation.
            if day in seen:raise DataError("source_disagreement","Duplicate daily index observations are not overwritten")
            seen.add(day)
            values={key:row[key] for key in ("open","high","low","close")}
            if any(type(value) not in (int,float) or not math.isfinite(value) or value<=0 for value in values.values()):
                raise DataError("invalid_schema","Index OHLC must be positive finite native levels")
            if not values["low"]<=min(values["open"],values["close"])<=max(values["open"],values["close"])<=values["high"]:
                raise DataError("invalid_schema","Native OHLC bounds disagree")
            selected.append({"trade_date":day,**values})
        if not selected:raise DataError("insufficient_coverage","Exact observation window is absent; no latest-value fallback")
        return Payload({"symbol":r.params["symbol"],**{k:spec[k] for k in ("name","unit","currency","market_timezone")},
            "adjustment":"none","frequency":"daily","native_identity":matches[0],"sdk_version":"1.20.4",
            "bars":sorted(selected,key=lambda row:row["trade_date"]),"snapshot_as_of_date":r.as_of_date,
            "availability_basis":"observed_current_snapshot","historical_vintage_certified":False,"raw_format":"decoded_sdk_response"},
            raw,obj["endpoint"],"verified",["Current captured native index levels only, without historical revision/publication certification",
                "No precomputed returns, scoring, risk thresholds or trading actions; core must check its frozen calendar"])
