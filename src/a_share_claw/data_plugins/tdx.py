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

# Identity aliases are exact, versioned and intentionally exclude ETFs and NDX.
INDEXES={
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
    if r.capability=="market.index_catalog":
        if p:raise DataError("invalid_request","Catalog has no free-form provider parameters")
        return {"operation":"catalog","kind":"international"}
    if set(p)!={"symbol","provider_code","start_date","end_date","count"}:
        raise DataError("invalid_request","Index history requires symbol/code/window/count")
    if not isinstance(p["symbol"],str) or p["symbol"] not in INDEXES:
        raise DataError("unsupported_instrument","Use the exact declared index identity, never ETF/NDX proxies")
    spec=INDEXES[p["symbol"]]
    if (not isinstance(p["provider_code"],str) or not re.fullmatch(r"[A-Za-z0-9._#-]{1,32}",p["provider_code"]) or
            spec["code"] is not None and p["provider_code"]!=spec["code"]):
        raise DataError("source_mismatch","Pin the exact native index code; international identity needs explicit catalog review")
    start,end=iso_date(p["start_date"]),iso_date(p["end_date"])
    if not start<=end<=r.as_of_date or type(p["count"]) is not int or not 1<=p["count"]<=600:
        raise DataError("invalid_request","Use a nonfuture window and bounded daily count 1..600")
    return {"operation":"bars","kind":spec["kind"],"market":spec["market"],"code":p["provider_code"],"count":p["count"]}


class EasyTDX(Provider):
    manifest=Manifest("easytdx",("market.index_catalog","market.index_daily_snapshot"),HOSTS,version="1.0.0")
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
        rows=obj.get("identity")
        if not isinstance(rows,list) or not rows:
            raise DataError("missing_identity","No native instrument identity was returned")
        if request["kind"]=="international" and len(rows)>=600:
            raise DataError("insufficient_coverage","International catalog may be truncated; do not certify identity from an incomplete page")
        if r.capability=="market.index_catalog":
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
            if day in seen:raise DataError("source_disagreement","Duplicate daily index observations are not overwritten")
            seen.add(day)
            values={key:row[key] for key in ("open","high","low","close")}
            if any(type(value) not in (int,float) or not math.isfinite(value) or value<=0 for value in values.values()):
                raise DataError("invalid_schema","Index OHLC must be positive finite native levels")
            if not values["low"]<=min(values["open"],values["close"])<=max(values["open"],values["close"])<=values["high"]:
                raise DataError("invalid_schema","Native OHLC bounds disagree")
            if r.params["start_date"]<=day<=r.params["end_date"]:selected.append({"trade_date":day,**values})
        if not selected:raise DataError("insufficient_coverage","Exact observation window is absent; no latest-value fallback")
        return Payload({"symbol":r.params["symbol"],**{k:spec[k] for k in ("name","unit","currency","market_timezone")},
            "adjustment":"none","frequency":"daily","native_identity":matches[0],"sdk_version":"1.20.4",
            "bars":sorted(selected,key=lambda row:row["trade_date"]),"snapshot_as_of_date":r.as_of_date,
            "availability_basis":"observed_current_snapshot","historical_vintage_certified":False,"raw_format":"decoded_sdk_response"},
            raw,obj["endpoint"],"verified",["Current captured native index levels only, without historical revision/publication certification",
                "No precomputed returns, scoring, risk thresholds or trading actions; core must check its frozen calendar"])
