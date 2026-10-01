# Jarvis (phone-ready)

## Option A - phone only, no PC (recommended)
1. Create a free account at metaapi.cloud, add your MT5 broker account, copy the **token** and **account ID**.
2. Put these 3 files in a GitHub repo and deploy on Streamlit Community Cloud (or Render).
3. In the app's Secrets / env vars set:
   ANTHROPIC_API_KEY, METAAPI_TOKEN, METAAPI_ACCOUNT_ID, JARVIS_PIN (choose a PIN)
4. Open the app URL on your phone, "Add to Home Screen". Upload a chart screenshot from your photos.

## Option B - your Windows PC runs it
1. Install MT5, log in, enable Tools > Options > Expert Advisors > Allow algorithmic trading.
2. `pip install -r requirements.txt` then `set JARVIS_PIN=1234` and `streamlit run jarvis.py`
3. Reach it from your phone anywhere: `cloudflared tunnel --url http://localhost:8501` and open the link.

**Always set JARVIS_PIN when the app is on the internet** - otherwise anyone with the link can trade your account.

Safeguards: demo by default, confirm checkbox, risk-% sizing, min confidence, stale-price check, SL/TP side check.
Cloud-mode auto lot size is approximate (exact only if the pair's quote currency = account currency); check the lots field.
Test on demo for weeks. Not financial advice.
