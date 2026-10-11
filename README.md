# Ultrahuman Ring -> Claude connector

Read-only MCP server. Claude se Ankit ka ring data (sleep, HRV, RHR, steps, recovery) puchh sakte hain.

## 1. App mein Partner ID daalo (ek baar)
Ultrahuman app -> Profile -> Settings -> Partner ID -> Ultrahuman ka diya Access Code daalo.

## 2. Token test karo (laptop pe)
    curl -H "Authorization: $UH_TOKEN" \
      "https://partner.ultrahuman.com/api/v1/metrics?email=<ultrahuman login email>&date=2026-10-10"
Email wahi hona chahiye jisse Ultrahuman app mein login hai.

## 3. Deploy (Render Blueprint, ~5 min)
- Ye folder ek PRIVATE GitHub repo mein push karo.
- Render -> New -> Blueprint -> repo chuno. render.yaml sab set kar dega.
- Render sirf 2 cheezein poochega: UH_TOKEN aur UH_EMAIL. MCP_SECRET_PATH khud ban jayega (Environment tab mein dikhega).

### Ya manually (Railway / koi bhi Docker host)
- Ye folder deploy karo (Dockerfile ready hai).
- Environment variables set karo: UH_TOKEN, UH_EMAIL (zaroori), MCP_SECRET_PATH
  (MCP_SECRET_PATH = `openssl rand -hex 24` se bani random string).
- HTTPS URL milega, jaise https://uh-mcp.onrender.com

Connector URL = https://<host>/<MCP_SECRET_PATH>/mcp

## 4. Claude mein jodo
Claude -> Settings -> Connectors -> Add custom connector -> upar wala URL.
Phir naye chat mein: "kal raat ki meri sleep aur HRV batao".

## Tools
- get_daily_metrics(day) - ek din ka data (default: kal)
- get_metrics_range(start_date, end_date) - max 31 din ka trend
- check_connection() - token chal raha hai ya nahi

## Security
- Token sirf env var mein. Code/git/WhatsApp mein kabhi nahi.
- Secret path hi lock hai: URL kisi ke saath share mat karna.
- Logs mein sirf date + status code, health values nahi.
