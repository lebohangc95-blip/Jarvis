"""
JARVIS - chart screenshot analyst + MT5 executor
Run:  streamlit run jarvis.py   (Windows PC with MetaTrader 5 terminal installed & logged in)
"""
import base64, json, os, re
import streamlit as st
import anthropic
import asyncio, hmac
try:
    import MetaTrader5 as mt5
except ImportError:  # not Windows -> use MetaApi cloud mode
    mt5 = None

MODEL = os.getenv("JARVIS_MODEL", "claude-sonnet-5-5")
MAGIC = 774411

PROMPT = """You are Jarvis, a disciplined technical analyst. Analyse the chart screenshot.
Return ONLY valid JSON, no markdown:
{"symbol": str|null, "timeframe": str|null, "trend": "up|down|range",
 "patterns": [str], "support": [num], "resistance": [num],
 "signal": "BUY|SELL|NO_TRADE", "entry": num|null, "stop_loss": num|null,
 "take_profit": num|null, "confidence": 0-100,
 "feedback": str, "reasoning": str}
Rules: read prices only from the price axis. If you cannot read them reliably,
or the setup is unclear, return NO_TRADE. Never invent levels. "feedback" should
critique the chart itself (missing timeframe, no indicators, cropped axis, etc.)
and the risks of the trade. Minimum 1:1.5 risk:reward or NO_TRADE."""


# ---------- analysis ----------
def analyse(img_bytes: bytes, mime: str, api_key: str) -> dict:
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime,
                                         "data": base64.b64encode(img_bytes).decode()}},
            {"type": "text", "text": PROMPT}]}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


# ---------- MT5 ----------
def mt5_connect(login, password, server):
    ok = mt5.initialize(login=int(login), password=password, server=server) if login \
        else mt5.initialize()
    if not ok:
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    return mt5.account_info()


def lot_size(symbol, entry, sl, risk_money):
    info = mt5.symbol_info(symbol)
    ticks = abs(entry - sl) / info.trade_tick_size
    loss_per_lot = ticks * info.trade_tick_value
    lots = risk_money / loss_per_lot if loss_per_lot else info.volume_min
    step = info.volume_step
    lots = max(info.volume_min, min(info.volume_max, round(lots / step) * step))
    return round(lots, 2)


def send_order(symbol, side, sl, tp, lots):
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Symbol {symbol} not found in Market Watch")
    tick = mt5.symbol_info_tick(symbol)
    buy = side == "BUY"
    price = tick.ask if buy else tick.bid
    if buy and not (sl < price < tp) or (not buy) and not (tp < price < sl):
        raise RuntimeError("SL/TP are on the wrong side of the live price - aborted")
    for fill in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        res = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lots,
            "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
            "price": price, "sl": sl, "tp": tp, "deviation": 20, "magic": MAGIC,
            "comment": "Jarvis", "type_time": mt5.ORDER_TIME_GTC, "type_filling": fill})
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return res
    raise RuntimeError(f"Order rejected: {res.retcode if res else mt5.last_error()} "
                       f"{getattr(res, 'comment', '')}")


async def _metaapi_trade(token, acct_id, symbol, side, entry, sl, tp, risk_pct, max_drift, lots_manual):
    from metaapi_cloud_sdk import MetaApi
    acc = await MetaApi(token).metatrader_account_api.get_account(acct_id)
    if acc.state != "DEPLOYED":
        await acc.deploy()
    await acc.wait_connected()
    conn = acc.get_rpc_connection()
    await conn.connect()
    await conn.wait_synchronized()
    info = await conn.get_account_information()
    spec = await conn.get_symbol_specification(symbol)
    px = await conn.get_symbol_price(symbol)
    mid = (px["ask"] + px["bid"]) / 2
    if abs(mid - entry) / entry * 100 > max_drift:
        raise RuntimeError(f"Live price {mid} drifted from screenshot entry - re-screenshot")
    buy = side == "BUY"
    price = px["ask"] if buy else px["bid"]
    if (buy and not sl < price < tp) or (not buy and not tp < price < sl):
        raise RuntimeError("SL/TP are on the wrong side of the live price - aborted")
    if lots_manual:
        lots = lots_manual
    else:  # approximation: exact only when the quote currency = account currency
        per_lot = abs(price - sl) * spec.get("contractSize", 100000)
        step = spec.get("volumeStep", 0.01)
        lots = (info["balance"] * risk_pct / 100) / per_lot
        lots = max(spec.get("minVolume", 0.01), min(spec.get("maxVolume", 100), round(lots / step) * step))
        lots = round(lots, 2)
    fn = conn.create_market_buy_order if buy else conn.create_market_sell_order
    res = await fn(symbol, lots, sl, tp, {"comment": "Jarvis", "magic": MAGIC})
    return lots, res


# ---------- UI ----------
st.set_page_config(page_title="Jarvis", page_icon="🤖", layout="centered")
st.markdown("<style>.stButton>button{width:100%;padding:.9rem;font-size:1.05rem}"
            ".block-container{padding:1rem .8rem}</style>", unsafe_allow_html=True)
PIN = os.getenv("JARVIS_PIN", "")
if PIN and st.session_state.get("pin_ok") != True:
    p = st.text_input("🔒 PIN", type="password")
    if p and hmac.compare_digest(p.encode(), PIN.encode()):
        st.session_state.pin_ok = True
        st.rerun()
    st.stop()
st.title("🤖 Jarvis")
st.caption("Chart screenshot → analysis → signal → MT5")

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Anthropic API key", os.getenv("ANTHROPIC_API_KEY", ""), type="password")
    st.subheader("Broker connection")
    mode = st.radio("Mode", ["Cloud (MetaApi)", "Local MT5 (Windows PC)"],
                    index=0 if mt5 is None else 1)
    login = pw = server = ""
    if mode.startswith("Cloud"):
        ma_token = st.text_input("MetaApi token", os.getenv("METAAPI_TOKEN", ""), type="password")
        ma_id = st.text_input("MetaApi account ID", os.getenv("METAAPI_ACCOUNT_ID", ""))
        acct_type = st.selectbox("Account type", ["Demo", "Live"])
    else:
        login = st.text_input("Login (blank = use open terminal)")
        pw = st.text_input("Password", type="password")
        server = st.text_input("Server")
    st.subheader("Risk")
    risk_pct = st.slider("Risk per trade (%)", 0.1, 2.0, 0.5, 0.1)
    min_conf = st.slider("Min confidence to allow trade", 0, 100, 65)
    max_drift = st.slider("Max price drift from entry (%)", 0.05, 1.0, 0.2, 0.05)
    allow_live = st.checkbox("Allow LIVE account (default: demo only)", False)

up = st.file_uploader("Upload chart screenshot", type=["png", "jpg", "jpeg", "webp"])
if up:
    st.image(up, use_container_width=True)
    if st.button("Analyse chart", type="primary", disabled=not api_key):
        with st.spinner("Jarvis is reading the chart..."):
            try:
                st.session_state.a = analyse(up.getvalue(), up.type, api_key)
            except Exception as e:
                st.error(f"Analysis failed: {e}")

a = st.session_state.get("a")
if a:
    sig = a.get("signal", "NO_TRADE")
    color = {"BUY": "green", "SELL": "red"}.get(sig, "gray")
    st.markdown(f"## :{color}[{sig}]  · confidence {a.get('confidence', 0)}%")
    c1, c2, c3 = st.columns(3)
    c1.metric("Entry", a.get("entry")); c2.metric("Stop loss", a.get("stop_loss"))
    c3.metric("Take profit", a.get("take_profit"))
    st.write(f"**{a.get('symbol')} · {a.get('timeframe')} · trend: {a.get('trend')}**")
    st.write("**Patterns:**", ", ".join(a.get("patterns", [])) or "none")
    st.write("**Support:**", a.get("support"), " **Resistance:**", a.get("resistance"))
    st.info(f"**Feedback:** {a.get('feedback')}")
    with st.expander("Reasoning"):
        st.write(a.get("reasoning"))

    if sig in ("BUY", "SELL"):
        st.divider()
        st.subheader("Execute on MT5")
        symbol = st.text_input("Broker symbol (check suffix, e.g. EURUSDm)", a.get("symbol") or "")
        entry = st.number_input("Entry", value=float(a["entry"]), format="%.5f")
        sl = st.number_input("Stop loss", value=float(a["stop_loss"]), format="%.5f")
        tp = st.number_input("Take profit", value=float(a["take_profit"]), format="%.5f")
        lots_manual = st.number_input("Lots (0 = auto from risk %)", 0.0, 100.0, 0.0, 0.01)
        confirm = st.checkbox("I have reviewed this trade and want to place it")
        if st.button("🚀 Place trade", disabled=not confirm):
            try:
                if a.get("confidence", 0) < min_conf:
                    raise RuntimeError(f"Confidence below your {min_conf}% minimum")
                if mode.startswith("Cloud"):
                    if acct_type == "Live" and not allow_live:
                        raise RuntimeError("Live account selected. Enable 'Allow LIVE' in sidebar.")
                    lots, res = asyncio.run(_metaapi_trade(
                        ma_token, ma_id, symbol, sig, entry, sl, tp, risk_pct, max_drift, lots_manual))
                    st.success(f"Order sent: {sig} {lots} lots {symbol}. {res}")
                else:
                    acc = mt5_connect(login, pw, server)
                    if acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO and not allow_live:
                        raise RuntimeError("Live account detected. Enable 'Allow LIVE' in sidebar.")
                    mt5.symbol_select(symbol, True)
                    tick = mt5.symbol_info_tick(symbol)
                    mid = (tick.ask + tick.bid) / 2
                    if abs(mid - entry) / entry * 100 > max_drift:
                        raise RuntimeError(f"Live price {mid} drifted from screenshot entry - re-screenshot")
                    lots = lots_manual or lot_size(symbol, mid, sl, acc.balance * risk_pct / 100)
                    res = send_order(symbol, sig, sl, tp, lots)
                    st.success(f"Order #{res.order} filled: {sig} {lots} lots {symbol} @ {res.price}")
            except Exception as e:
                st.error(str(e))
            finally:
                if mt5:
                    mt5.shutdown()
