"""Check the optional SDK actually imports in an isolated no-acquisition child."""
import os,subprocess,sys
from importlib.metadata import PackageNotFoundError,version

import pytest


def test_optional_market_install_is_pinned_and_importable(tmp_path):
    try:installed=version('easy-tdx')
    except PackageNotFoundError:pytest.skip('Optional market extra is not installed')
    assert installed=='1.20.4'
    env={k:v for k,v in os.environ.items() if k in {'PATH','LANG','LC_ALL','SYSTEMROOT','SYSTEMDRIVE','HOME','USERPROFILE','HOMEDRIVE','HOMEPATH'}}
    env['EASY_TDX_CONFIG_DIR']=str(tmp_path)
    code='from easy_tdx import MacClient,MacExClient,Market,ExMarket,Period,Adjust; assert int(Period.DAILY)==4; assert int(Adjust.NONE)==0'
    subprocess.run([sys.executable,'-I','-c',code],env=env,check=True,capture_output=True,timeout=30)
