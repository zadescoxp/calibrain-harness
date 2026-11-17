from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MT5 = ROOT / "mt5"
EA = MT5 / "Experts" / "CalibrainAgents" / "CalibrainAgents.mq5"


def test_six_agent_modules_are_wired_into_the_expert_advisor():
    source = EA.read_text(encoding="utf-8")
    for name in ("Atlas", "Vega", "Orion", "Titan", "Luna", "Nova"):
        module = MT5 / "include" / "Calibrain" / "Agents" / f"{name}.mqh"
        assert module.is_file()
        assert f"Agents/{name}.mqh" in source
        assert f"g_{name.lower()}." in source


def test_order_placement_is_disabled_by_default_and_demo_guarded():
    source = EA.read_text(encoding="utf-8")
    assert "input bool            InpEnableTrading=false;" in source
    assert "if(InpEnableTrading && !IsSafeTradingEnvironment())" in source
    assert "ACCOUNT_TRADE_MODE_DEMO" in source
    assert "MQLInfoInteger(MQL_TESTER)" in source


def test_risk_limits_and_exit_management_are_connected():
    source = EA.read_text(encoding="utf-8")
    nova = (MT5 / "include" / "Calibrain" / "Agents" / "Nova.mqh").read_text(encoding="utf-8")
    luna = (MT5 / "include" / "Calibrain" / "Agents" / "Luna.mqh").read_text(encoding="utf-8")
    assert "g_nova.Approve(" in source
    assert "g_luna.Manage(" in source
    assert "maximum_daily_loss_pct" in nova
    assert "maximum_portfolio_risk_pct" in nova
    assert "trade.PositionModify(" in luna
    assert "SYMBOL_TRADE_STOPS_LEVEL" in source


def test_setup_guide_and_installer_are_present():
    assert (MT5 / "README.md").is_file()
    installer = (MT5 / "tools" / "Install-MT5.ps1").read_text(encoding="utf-8")
    assert "TerminalDataPath" in installer
    assert "CalibrainAgents" in installer
    assert "MT5_Trading_Engine.mq5` أحادي الملف محفوظ" in (MT5 / "README.md").read_text(encoding="utf-8")
