"""
AutoPiE（MocoStudio）商業授權原型伺服器 — 互動式測試網頁介面
提供：
  1. /                模擬 MocoStudio 客戶端（支援真實瀏覽器 OAuth PKCE、換機、24h 緊急延長、獎勵券領取）
  2. /oauth/authorize 模擬瀏覽器帳號登入與授權頁
  3. /admin           營運管理後台（免刷卡模擬開通 3 個月／1 年、發放獎勵券、風控稽核與時間快轉）
"""

from __future__ import annotations

import html

BASE_CSS = """
:root {
  --bg: #0f131a;
  --panel: #171d27;
  --panel2: #1e2633;
  --border: #2c3749;
  --text: #e6edf7;
  --muted: #93a3ba;
  --accent: #3b82f6;
  --ok: #22c55e;
  --warn: #f59e0b;
  --danger: #ef4444;
}
* { box-sizing: border-box; }
body {
  margin: 0; font-family: "Segoe UI", "Microsoft JhengHei", sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.5;
}
a { color: #60a5fa; text-decoration: none; }
header {
  display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
  padding: 14px 24px; background: var(--panel); border-bottom: 1px solid var(--border);
}
.brand { font-weight: 700; font-size: 18px; letter-spacing: 0.4px; }
.brand span { color: var(--accent); }
nav { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.nav-link {
  padding: 7px 13px; border-radius: 7px; border: 1px solid var(--border);
  background: var(--panel2); color: var(--text); font-size: 13px;
}
.nav-link.active { border-color: var(--accent); background: rgba(59,130,246,0.18); }
main { max-width: 1180px; margin: 22px auto; padding: 0 20px 48px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 18px; }
.card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  padding: 18px 20px; margin-bottom: 18px;
}
.card h2 { margin: 0 0 12px; font-size: 16px; display: flex; justify-content: space-between; align-items: center; }
.card h3 { margin: 14px 0 8px; font-size: 14px; color: var(--muted); }
label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
input, select {
  width: 100%; padding: 9px 11px; border-radius: 7px; border: 1px solid var(--border);
  background: #0c1017; color: var(--text); font-size: 13px; margin-bottom: 10px;
}
button {
  padding: 8px 14px; border-radius: 7px; border: 1px solid var(--border);
  background: var(--panel2); color: var(--text); cursor: pointer; font-size: 13px; font-weight: 600;
}
button:hover { filter: brightness(1.15); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn-ok { background: #15803d; border-color: #16a34a; color: #fff; }
.btn-warn { background: #b45309; border-color: #d97706; color: #fff; }
.btn-danger { background: #b91c1c; border-color: #dc2626; color: #fff; }
.btn-sm { padding: 5px 10px; font-size: 12px; }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700;
}
.b-ok { background: rgba(34,197,94,0.18); color: #4ade80; border: 1px solid rgba(34,197,94,0.4); }
.b-warn { background: rgba(245,158,11,0.18); color: #fbbf24; border: 1px solid rgba(245,158,11,0.4); }
.b-danger { background: rgba(239,68,68,0.18); color: #f87171; border: 1px solid rgba(239,68,68,0.4); }
.b-muted { background: rgba(148,163,184,0.15); color: var(--muted); border: 1px solid var(--border); }
.kv { display: grid; grid-template-columns: 140px 1fr; gap: 6px 10px; font-size: 13px; }
.kv .k { color: var(--muted); }
.mono { font-family: Consolas, Monaco, monospace; font-size: 12px; word-break: break-all; }
.banner {
  padding: 11px 14px; border-radius: 8px; margin-bottom: 14px; font-size: 13px;
  border: 1px solid var(--border); background: var(--panel2);
}
.banner.ok { border-color: rgba(34,197,94,0.5); background: rgba(34,197,94,0.12); color: #bbf7d0; }
.banner.warn { border-color: rgba(245,158,11,0.6); background: rgba(245,158,11,0.14); color: #fde68a; }
.banner.danger { border-color: rgba(239,68,68,0.6); background: rgba(239,68,68,0.14); color: #fecaca; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; background: rgba(255,255,255,0.02); }
.clock-bar {
  background: #121824; border-bottom: 1px solid var(--border); padding: 8px 24px;
  display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 12px;
}
"""


def render_client_page(base_url: str) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>AutoPiE 授權原型 — 模擬 MocoStudio 客戶端</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header>
  <div class="brand">AutoPiE <span>License Cloud Prototype</span></div>
  <nav>
    <a class="nav-link active" href="/">模擬 MocoStudio 客戶端</a>
    <a class="nav-link" href="/admin">營運管理後台 (Admin)</a>
    <a class="nav-link" href="/.well-known/jwks.json" target="_blank">JWKS 公鑰</a>
  </nav>
</header>

<div class="clock-bar">
  <div>模擬伺服器時間：<strong id="serverTimeText">讀取中...</strong> <span id="clockOffsetBadge" class="badge b-muted">偏移 0h</span></div>
  <div class="row">
    <span style="color:var(--muted)">時間快轉測試：</span>
    <button class="btn-sm" onclick="advanceClock(25)">+25h（滑動續期）</button>
    <button class="btn-sm" onclick="advanceClock(73)">+73h（租約過期）</button>
    <button class="btn-sm" onclick="advanceClock(145)">+145h（過換機冷卻）</button>
    <button class="btn-sm" onclick="advanceClock(744)">+31天（過緊急延長冷卻）</button>
    <button class="btn-sm" onclick="advanceClock(2184)">+91天（季訂到期/重置緊急換機）</button>
    <button class="btn-sm btn-warn" onclick="resetClock()">重置時間</button>
  </div>
</div>

<main>
  <div id="alertBox"></div>

  <div class="grid">
    <!-- 左欄：模擬裝置與登入控制 -->
    <div class="card">
      <h2>1. 模擬片場電腦 &amp; 瀏覽器登入 <span id="gateBadge" class="badge b-danger">未登入</span></h2>
      <label>選擇目前模擬的電腦（測試換機冷卻與緊急換機用）</label>
      <select id="deviceSelect" onchange="onDeviceChange()">
        <option value="STUDIO-PC-A-0011223344556677|控台電腦 A（主控）">控台電腦 A（主控）— STUDIO-PC-A-0011223344556677</option>
        <option value="BACKUP-LAPTOP-B-8899AABBCCDD|備用筆電 B（片場備機）">備用筆電 B（片場備機）— BACKUP-LAPTOP-B-8899AABBCCDD</option>
        <option value="FIELD-PC-C-FFEEDDCCBBAA9988|第三台電腦 C（測試超額換機）">第三台電腦 C（測試超額換機）— FIELD-PC-C-FFEEDDCCBBAA9988</option>
      </select>

      <div class="row" style="margin: 10px 0 14px;">
        <button class="btn-primary" onclick="startOAuthLogin()">Sign in（開啟瀏覽器 OAuth 登入）</button>
        <button onclick="renewLease()">背景滑動續期（Renew 72h）</button>
        <button class="btn-danger" onclick="signOutLocal()">Sign out</button>
      </div>

      <div id="cooldownBox" style="display:none;" class="banner warn">
        <div id="cooldownText" style="margin-bottom:8px;"></div>
        <button id="btnEmergencyTransfer" class="btn-warn" onclick="activateDevice(true)">使用免冷卻緊急換機（每季 1 次）</button>
      </div>

      <div id="offlineGraceBox" style="display:none;" class="banner warn">
        <div style="margin-bottom:8px;"><strong>租約已到期（安全唯讀：保留存檔與 STOP，鎖定新運動）</strong><br/>若片場暫時斷網，您可自行決定是否動用本機 24 小時緊急延長（每 30 天限 1 次）。</div>
        <button id="btnOfflineGrace" class="btn-warn" onclick="triggerOfflineGrace()">主動啟用 24 小時緊急延長</button>
      </div>

      <h3>模擬實機准入狀態（MocoLicenseGate）</h3>
      <div class="kv">
        <div class="k">新運動 / 編輯</div><div id="motionState"><span class="badge b-danger">鎖定</span></div>
        <div class="k">存檔 / STOP</div><div><span class="badge b-ok">永遠允許</span></div>
        <div class="k">本機離線延長狀態</div><div id="localGraceState" class="mono">未啟用</div>
      </div>
    </div>

    <!-- 右欄：72 小時租約與 JWT 內容 -->
    <div class="card">
      <h2>2. 目前 72 小時租約狀態 <span id="subStatusBadge" class="badge b-muted">--</span></h2>
      <div class="kv">
        <div class="k">登入帳號</div><div id="vEmail" class="mono">--</div>
        <div class="k">訂閱方案</div><div id="vPlan">--</div>
        <div class="k">訂閱到期日</div><div id="vSubEnd">--</div>
        <div class="k">租約剩餘時間</div><div id="vLeaseRem">--</div>
        <div class="k">24h 緊急延長資格</div><div id="vGraceAvail">--</div>
        <div class="k">本季緊急換機剩餘</div><div id="vEmerSwap">--</div>
        <div class="k">RS256 簽章驗證</div><div id="vJwtVerify">--</div>
      </div>
      <h3>伺服器簽發的 JWT（自動保存，無須手動匯入授權檔）</h3>
      <div id="vRawJwt" class="mono" style="background:#0b0f16;padding:10px;border-radius:8px;max-height:110px;overflow:auto;border:1px solid var(--border);color:#93c5fd;">尚未領取租約</div>
    </div>
  </div>

  <!-- 帳號內待領取獎勵券 -->
  <div class="card">
    <h2>3. 帳號專屬待領取獎勵券（Claimable Rewards）
      <button class="btn-sm" onclick="refreshAccount()">重新整理帳號狀態</button>
    </h2>
    <p style="font-size:12px;color:var(--muted);margin-top:0;">
      規則：所有獎勵券強制綁定本帳號、不可轉送；<strong>僅限訂閱有效期間內套用，訂閱到期即自動失效</strong>。新用戶首購自動配發 1 張 14 天延長券。
    </p>
    <div id="rewardsArea"><span style="color:var(--muted);font-size:13px;">登入後顯示此帳號的待領取獎勵券。</span></div>
  </div>
</main>

<script>
const BASE = {base_url!r};
let serverNow = Math.floor(Date.now() / 1000);

function fmtTime(ts) {{
  if (!ts) return '--';
  return new Date(ts * 1000).toLocaleString('zh-TW', {{ hour12: false }});
}}
function fmtDuration(sec) {{
  if (sec <= 0) return '已到期';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return (d > 0 ? d + ' 天 ' : '') + h + ' 小時 ' + m + ' 分鐘';
}}
function showAlert(msg, type = 'ok') {{
  document.getElementById('alertBox').innerHTML = `<div class="banner ${{type}}">${{msg}}</div>`;
}}
function currentDevice() {{
  const [id, label] = document.getElementById('deviceSelect').value.split('|');
  return {{ id, label }};
}}
function storageKey(devId) {{
  return 'autopie_client_state_' + devId;
}}
function loadLocalState() {{
  const dev = currentDevice();
  try {{
    return JSON.parse(localStorage.getItem(storageKey(dev.id)) || '{{}}');
  }} catch {{ return {{}}; }}
}}
function saveLocalState(st) {{
  const dev = currentDevice();
  localStorage.setItem(storageKey(dev.id), JSON.stringify(st));
}}
function onDeviceChange() {{
  document.getElementById('cooldownBox').style.display = 'none';
  renderLocalState();
  refreshAccount();
}}

// PKCE 工具
async function sha256Base64Url(str) {{
  const buf = new TextEncoder().encode(str);
  const hash = await crypto.subtle.digest('SHA-256', buf);
  return btoa(String.fromCharCode(...new Uint8Array(hash)))
    .replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
}}
function randomVerifier() {{
  const arr = new Uint8Array(32);
  crypto.getRandomValues(arr);
  return btoa(String.fromCharCode(...arr)).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
}}

async function startOAuthLogin() {{
  const verifier = randomVerifier();
  const challenge = await sha256Base64Url(verifier);
  const dev = currentDevice();
  sessionStorage.setItem('pkce_verifier', verifier);
  sessionStorage.setItem('pkce_device_value', document.getElementById('deviceSelect').value);
  const redirect = window.location.origin + '/';
  window.location.href = `/oauth/authorize?code_challenge=${{encodeURIComponent(challenge)}}&redirect_uri=${{encodeURIComponent(redirect)}}`;
}}

async function handleOAuthCallbackIfPresent() {{
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  if (!code) return;
  const savedDev = sessionStorage.getItem('pkce_device_value');
  if (savedDev) document.getElementById('deviceSelect').value = savedDev;
  const verifier = sessionStorage.getItem('pkce_verifier') || '';
  window.history.replaceState({{}}, '', '/');

  const res = await fetch('/v1/auth/oauth/exchange', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ code, code_verifier: verifier }})
  }});
  const data = await res.json();
  if (!res.ok) {{
    showAlert('瀏覽器登入交換憑證失敗：' + (data.message || res.status), 'danger');
    return;
  }}
  const st = loadLocalState();
  st.access_token = data.access_token;
  st.email = data.user.email;
  saveLocalState(st);
  showAlert(`瀏覽器 OAuth 登入成功（${{data.user.email}}），正在向雲端伺服器領取 72 小時租約...`, 'ok');
  await activateDevice(false);
}}

async function activateDevice(useEmergency) {{
  const st = loadLocalState();
  if (!st.access_token) {{
    showAlert('請先點擊 Sign in 完成瀏覽器登入。', 'warn');
    return;
  }}
  const dev = currentDevice();
  if (useEmergency && !confirm('確定要動用本季（90 天內限 1 次、不可累加）的免冷卻緊急換機嗎？舊機授權將會立即撤銷。')) {{
    return;
  }}
  const res = await fetch('/v1/license/activate', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + st.access_token }},
    body: JSON.stringify({{
      device_id: dev.id,
      device_label: dev.label,
      use_emergency_transfer: useEmergency
    }})
  }});
  const data = await res.json();
  if (res.status === 409 && data.code === 'TRANSFER_COOLDOWN') {{
    const box = document.getElementById('cooldownBox');
    box.style.display = 'block';
    document.getElementById('cooldownText').innerHTML =
      `<strong>換機冷卻攔截：</strong>${{data.message}}<br/>目前綁定裝置：${{data.old_device_label || data.old_device_id}}。<br/>本季免冷卻緊急換機剩餘：<strong>${{data.emergency_transfer_remaining}} / 1 次</strong>`;
    document.getElementById('btnEmergencyTransfer').disabled = (data.emergency_transfer_remaining <= 0);
    showAlert(data.message, 'warn');
    return;
  }}
  if (!res.ok) {{
    showAlert(`啟用失敗（${{data.code || res.status}}）：${{data.message}}`, 'danger');
    return;
  }}
  document.getElementById('cooldownBox').style.display = 'none';
  st.lease_jwt = data.lease_jwt;
  st.payload = data.payload;
  st.offline_grace_until = null;
  st.pending_grace_report = null;
  saveLocalState(st);
  showAlert(data.notice || `已自動領取 72 小時 RS256 租約（事件：${{data.event_type}}）！`, 'ok');
  await fetchClock();
  renderLocalState();
  await refreshAccount();
}}

async function renewLease() {{
  const st = loadLocalState();
  if (!st.access_token) {{
    showAlert('尚未登入，請先按 Sign in。', 'warn');
    return;
  }}
  const dev = currentDevice();
  const res = await fetch('/v1/license/renew', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + st.access_token }},
    body: JSON.stringify({{
      device_id: dev.id,
      offline_grace_used_at: st.pending_grace_report || null
    }})
  }});
  const data = await res.json();
  if (!res.ok) {{
    showAlert(`續期被拒絕（${{data.code || res.status}}）：${{data.message}}`, 'danger');
    if (res.status === 403) {{
      st.lease_jwt = null;
      st.payload = null;
      saveLocalState(st);
      renderLocalState();
    }}
    return;
  }}
  const reported = !!st.pending_grace_report;
  st.lease_jwt = data.lease_jwt;
  st.payload = data.payload;
  st.offline_grace_until = null;
  st.pending_grace_report = null;
  saveLocalState(st);
  showAlert(reported ? '已續期 72 小時租約，並已將先前的本機緊急延長紀錄回報至伺服器（啟動 30 天冷卻）。' : '背景滑動續期成功！租約已重新推滿 72 小時。', 'ok');
  await fetchClock();
  renderLocalState();
  await refreshAccount();
}}

function triggerOfflineGrace() {{
  const st = loadLocalState();
  if (!st.payload || !st.payload.grace_available) {{
    showAlert('目前無可用的 24 小時緊急延長額度（每 30 天限 1 次）。', 'danger');
    return;
  }}
  if (!confirm('確定要啟用 24 小時本機緊急延長嗎？啟用後未來 30 天內無法再次使用，下次連網時將自動回報伺服器。')) {{
    return;
  }}
  st.offline_grace_until = serverNow + 24 * 3600;
  st.pending_grace_report = serverNow;
  st.payload.grace_available = false;
  saveLocalState(st);
  showAlert('已啟用本機 24 小時緊急延長！您可繼續工作 24 小時（下次連網續期會自動回報伺服器）。', 'warn');
  renderLocalState();
}}

function signOutLocal() {{
  const dev = currentDevice();
  localStorage.removeItem(storageKey(dev.id));
  document.getElementById('cooldownBox').style.display = 'none';
  showAlert('已登出本機工作階段。', 'ok');
  renderLocalState();
  document.getElementById('rewardsArea').innerHTML = '<span style="color:var(--muted);font-size:13px;">登入後顯示此帳號的待領取獎勵券。</span>';
}}

function renderLocalState() {{
  const st = loadLocalState();
  const p = st.payload;
  const gateBadge = document.getElementById('gateBadge');
  const motionEl = document.getElementById('motionState');
  const graceBox = document.getElementById('offlineGraceBox');
  const localGraceEl = document.getElementById('localGraceState');

  if (!p) {{
    gateBadge.className = 'badge b-danger'; gateBadge.textContent = '未登入';
    motionEl.innerHTML = '<span class="badge b-danger">鎖定（需登入）</span>';
    document.getElementById('subStatusBadge').textContent = '--';
    document.getElementById('vEmail').textContent = st.email || '--';
    document.getElementById('vPlan').textContent = '--';
    document.getElementById('vSubEnd').textContent = '--';
    document.getElementById('vLeaseRem').textContent = '--';
    document.getElementById('vGraceAvail').textContent = '--';
    document.getElementById('vEmerSwap').textContent = '--';
    document.getElementById('vJwtVerify').textContent = '--';
    document.getElementById('vRawJwt').textContent = '尚未領取租約';
    graceBox.style.display = 'none';
    localGraceEl.textContent = '未啟用';
    return;
  }}

  const effectiveExp = Math.max(p.exp, st.offline_grace_until || 0);
  const rem = effectiveExp - serverNow;
  const inGrace = st.offline_grace_until && serverNow < st.offline_grace_until && serverNow >= p.exp;

  if (rem > 24 * 3600) {{
    gateBadge.className = 'badge b-ok'; gateBadge.textContent = '授權有效';
    motionEl.innerHTML = '<span class="badge b-ok">允許執行</span>';
    graceBox.style.display = 'none';
  }} else if (rem > 0) {{
    gateBadge.className = 'badge b-warn';
    gateBadge.textContent = inGrace ? '24h 緊急延長中' : '24h 內到期警告（黃燈）';
    motionEl.innerHTML = '<span class="badge b-ok">允許執行（提醒續期）</span>';
    graceBox.style.display = 'none';
  }} else {{
    gateBadge.className = 'badge b-danger'; gateBadge.textContent = '租約已過期';
    motionEl.innerHTML = '<span class="badge b-danger">已鎖定新運動與編輯</span>';
    graceBox.style.display = 'block';
    document.getElementById('btnOfflineGrace').disabled = !p.grace_available;
  }}

  const subBadge = document.getElementById('subStatusBadge');
  subBadge.className = p.sub_status === 'active' ? 'badge b-ok' : 'badge b-warn';
  subBadge.textContent = p.sub_status === 'past_due' ? '扣款失敗寬限中' : p.sub_status;

  document.getElementById('vEmail').textContent = `${{p.email}} (${{p.name}})`;
  document.getElementById('vPlan').textContent = p.plan === 'annual' ? '一年期（全功能）' : '三個月期（全功能）';
  document.getElementById('vSubEnd').textContent = fmtTime(p.sub_end);
  document.getElementById('vLeaseRem').textContent = fmtDuration(rem) + (inGrace ? ' [緊急延長]' : '');
  document.getElementById('vGraceAvail').innerHTML = p.grace_available
    ? '<span class="badge b-ok">可用（+24h）</span>'
    : '<span class="badge b-muted">冷卻中（30天限1次）</span>';
  document.getElementById('vEmerSwap').textContent = `${{p.emergency_transfer_remaining}} / 1 次（每 90 天不累加）`;
  document.getElementById('vJwtVerify').innerHTML = '<span class="badge b-ok">RS256 簽章有效（kid: autopie-proto-2026-01）</span>';
  document.getElementById('vRawJwt').textContent = st.lease_jwt || '';
  localGraceEl.textContent = st.pending_grace_report
    ? `已觸發，截止 ${{fmtTime(st.offline_grace_until)}}（待連網回報）`
    : '未啟用';
}}

async function refreshAccount() {{
  const st = loadLocalState();
  if (!st.access_token) return;
  const res = await fetch('/v1/account/status', {{
    headers: {{ 'Authorization': 'Bearer ' + st.access_token }}
  }});
  if (!res.ok) return;
  const data = await res.json();
  serverNow = data.server_time;
  const area = document.getElementById('rewardsArea');
  if (!data.rewards || data.rewards.length === 0) {{
    area.innerHTML = '<span style="color:var(--muted);font-size:13px;">此帳號目前沒有獎勵券。</span>';
    return;
  }}
  area.innerHTML = `<table>
    <thead><tr><th>獎勵內容</th><th>天數</th><th>狀態</th><th>操作</th></tr></thead>
    <tbody>
      ${{data.rewards.map(r => `<tr>
        <td>${{r.reason}}</td>
        <td><strong>+${{r.extend_days}} 天</strong></td>
        <td><span class="badge ${{r.status==='available'?'b-ok':(r.status==='claimed'?'b-muted':'b-danger')}}">${{r.status}}</span></td>
        <td>${{r.status === 'available'
          ? `<button class="btn-ok btn-sm" onclick="claimReward('${{r.id}}', ${{r.extend_days}})">立即套用</button>`
          : (r.claimed_at ? '已於 ' + fmtTime(r.claimed_at) + ' 套用' : '--')}}</td>
      </tr>`).join('')}}
    </tbody>
  </table>`;
}}

async function claimReward(rewardId, days) {{
  const st = loadLocalState();
  if (!confirm(`確定要立即套用這張 +${{days}} 天延長獎勵券嗎？套用後將直接延長訂閱到期日並同步順延下次扣款日。`)) return;
  const res = await fetch(`/v1/rewards/${{rewardId}}/claim`, {{
    method: 'POST',
    headers: {{ 'Authorization': 'Bearer ' + st.access_token }}
  }});
  const data = await res.json();
  if (!res.ok) {{
    showAlert(`領取失敗：${{data.message}}`, 'danger');
    await refreshAccount();
    return;
  }}
  showAlert(`成功套用 +${{data.extend_days}} 天延長獎勵券！新訂閱到期日：${{fmtTime(data.new_subscription_end)}}`, 'ok');
  await refreshAccount();
  await renewLease();
}}

async function fetchClock() {{
  const res = await fetch('/v1/debug/clock');
  const data = await res.json();
  serverNow = data.server_time;
  document.getElementById('serverTimeText').textContent = fmtTime(serverNow);
  document.getElementById('clockOffsetBadge').textContent = `偏移 +${{data.clock_offset_hours}}h`;
}}
async function advanceClock(hours) {{
  await fetch('/v1/debug/clock/advance', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ hours }})
  }});
  await fetchClock();
  renderLocalState();
  await refreshAccount();
}}
async function resetClock() {{
  await fetch('/v1/debug/clock/reset', {{ method: 'POST' }});
  await fetchClock();
  renderLocalState();
  await refreshAccount();
}}

(async () => {{
  await fetchClock();
  await handleOAuthCallbackIfPresent();
  renderLocalState();
  await refreshAccount();
}})();
</script>
</body>
</html>"""


def render_oauth_page(code_challenge: str, redirect_uri: str, users: list[dict]) -> str:
    options = "\n".join(
        f'<option value="{html.escape(u["email"])}">{html.escape(u["email"])} — {html.escape(u["display_name"])}</option>'
        for u in users
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>AutoPiE 帳號登入（OAuth 2.0 PKCE 模擬）</title>
<style>{BASE_CSS}
.auth-box {{ max-width: 460px; margin: 60px auto; }}
</style>
</head>
<body>
<main class="auth-box">
  <div class="card">
    <h2>AutoPiE 雲端帳號登入</h2>
    <p style="font-size:13px;color:var(--muted);">
      這模擬客戶點擊 MocoStudio 的 <strong>Sign in</strong> 後，系統自動開啟瀏覽器進行 OAuth 2.0 + PKCE 認證的畫面。登入完成將自動跳回 MocoStudio 並於背景領取 72 小時租約（無須手動匯入授權檔）。
    </p>
    <form method="POST" action="/oauth/authorize">
      <input type="hidden" name="code_challenge" value="{html.escape(code_challenge)}" />
      <input type="hidden" name="redirect_uri" value="{html.escape(redirect_uri)}" />
      <label>選擇已開通的模擬帳號（等同 Google / Email 登入）</label>
      <select name="email" required>{options}</select>
      <div class="row" style="margin-top:14px;">
        <button type="submit" class="btn-primary" style="width:100%;">確認授權並返回 MocoStudio</button>
      </div>
    </form>
    <div style="margin-top:12px;font-size:12px;color:var(--muted);">
      找不到帳號？請先到 <a href="/admin">營運管理後台</a> 開通 3 個月或 1 年訂閱。
    </div>
  </div>
</main>
</body>
</html>"""


def render_admin_page() -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>AutoPiE 授權原型 — 營運管理後台</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header>
  <div class="brand">AutoPiE <span>Admin Console（營運與風控後台）</span></div>
  <nav>
    <a class="nav-link" href="/">模擬 MocoStudio 客戶端</a>
    <a class="nav-link active" href="/admin">營運管理後台 (Admin)</a>
    <a class="nav-link" href="/.well-known/jwks.json" target="_blank">JWKS 公鑰</a>
  </nav>
</header>

<div class="clock-bar">
  <div>模擬伺服器時間：<strong id="serverTimeText">讀取中...</strong> <span id="clockOffsetBadge" class="badge b-muted">偏移 0h</span></div>
  <div class="row">
    <button class="btn-sm" onclick="advanceClock(73)">+73h</button>
    <button class="btn-sm" onclick="advanceClock(145)">+145h</button>
    <button class="btn-sm" onclick="advanceClock(2184)">+91天</button>
    <button class="btn-sm btn-warn" onclick="resetClock()">重置時間</button>
    <button class="btn-sm btn-primary" onclick="loadOverview()">重新整理</button>
  </div>
</div>

<main>
  <div id="alertBox"></div>

  <div class="grid">
    <div class="card">
      <h2>開通訂閱（免刷卡模擬）</h2>
      <label>客戶 Email</label>
      <input id="newEmail" placeholder="例如：film-team@studio.com" />
      <label>客戶名稱 / 工作室名稱</label>
      <input id="newName" placeholder="例如：台北片場團隊" />
      <label>訂閱週期（全機型全功能相同）</label>
      <select id="newPlan">
        <option value="quarterly">三個月期（90 天）</option>
        <option value="annual">一年期（365 天）</option>
      </select>
      <button class="btn-primary" onclick="createSubscription()">開通訂閱（新用戶自動送 14 天延長券）</button>
    </div>

    <div class="card">
      <h2>發放帳號專屬延長獎勵券（不可轉送）</h2>
      <label>目標租戶帳號</label>
      <select id="rewardUserSelect"></select>
      <label>延長天數</label>
      <select id="rewardDays">
        <option value="14">14 天（兩星期延長券）</option>
        <option value="30">30 天（一個月延長券）</option>
      </select>
      <label>發放原因</label>
      <input id="rewardReason" value="感謝回饋：官方延長獎勵券" />
      <button class="btn-ok" onclick="grantReward()">發放至該帳號待領取匣</button>
    </div>
  </div>

  <div class="card">
    <h2>租戶訂閱與席位管理</h2>
    <div id="usersTableArea">讀取中...</div>
  </div>

  <div class="card">
    <h2>登入、換機與風控稽核日誌（Audit Logs）</h2>
    <p style="font-size:12px;color:var(--muted);margin-top:0;">
      所有登入、換機冷卻攔截、緊急換機、本機緊急延長回報與異常事件均即時記錄於此，方便偵測異常頻繁換機或破解嘗試。
    </p>
    <div id="logsTableArea">讀取中...</div>
  </div>
</main>

<script>
function fmtTime(ts) {{
  if (!ts) return '--';
  return new Date(ts * 1000).toLocaleString('zh-TW', {{ hour12: false }});
}}
function showAlert(msg, type = 'ok') {{
  document.getElementById('alertBox').innerHTML = `<div class="banner ${{type}}">${{msg}}</div>`;
}}

async function apiPost(url, body) {{
  const res = await fetch(url, {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(body || {{}})
  }});
  const data = await res.json();
  if (!res.ok) {{
    showAlert(data.message || '操作失敗', 'danger');
    return null;
  }}
  return data;
}}

async function createSubscription() {{
  const email = document.getElementById('newEmail').value.trim();
  const display_name = document.getElementById('newName').value.trim();
  const plan = document.getElementById('newPlan').value;
  const data = await apiPost('/v1/admin/subscriptions', {{ email, display_name, plan }});
  if (!data) return;
  showAlert(`已開通 ${{data.email}}（${{plan === 'annual' ? '一年期' : '三個月期'}}）` + (data.new_user_reward_issued ? '，並已自動發放新用戶 14 天延長券！' : '。'), 'ok');
  document.getElementById('newEmail').value = '';
  document.getElementById('newName').value = '';
  await loadOverview();
}}

async function grantReward() {{
  const user_id = document.getElementById('rewardUserSelect').value;
  const extend_days = Number(document.getElementById('rewardDays').value);
  const reason = document.getElementById('rewardReason').value.trim();
  const data = await apiPost('/v1/admin/rewards/grant', {{ user_id, extend_days, reason }});
  if (!data) return;
  showAlert(`已發放 +${{extend_days}} 天帳號專屬獎勵券！`, 'ok');
  await loadOverview();
}}

async function revokeReward(reward_id) {{
  const data = await apiPost('/v1/admin/rewards/revoke', {{ reward_id }});
  if (!data) return;
  showAlert('已撤銷未領取獎勵券。', 'warn');
  await loadOverview();
}}

async function setPastDue(user_id) {{
  const data = await apiPost('/v1/admin/subscriptions/past-due', {{ user_id }});
  if (!data) return;
  showAlert('已模擬扣款失敗，進入 4 天付款寬限期（期間不鎖機，僅顯示提醒）。', 'warn');
  await loadOverview();
}}

async function toggleRevoke(seat_id, revoke) {{
  const data = await apiPost('/v1/admin/seats/revoke', {{ seat_id, revoke, reason: '風控審查撤銷' }});
  if (!data) return;
  showAlert(revoke ? '已撤銷該席位（下次續期或重新登入立即失效）。' : '已恢復該席位。', revoke ? 'danger' : 'ok');
  await loadOverview();
}}

async function resetCooldown(seat_id) {{
  const data = await apiPost('/v1/admin/seats/reset-cooldown', {{ seat_id }});
  if (!data) return;
  showAlert('已人工特批解除 144 小時換機冷卻。', 'warn');
  await loadOverview();
}}

async function advanceClock(hours) {{
  await apiPost('/v1/debug/clock/advance', {{ hours }});
  await loadOverview();
}}
async function resetClock() {{
  await apiPost('/v1/debug/clock/reset', {{}});
  await loadOverview();
}}

async function loadOverview() {{
  const res = await fetch('/v1/admin/overview');
  const data = await res.json();
  document.getElementById('serverTimeText').textContent = fmtTime(data.server_time);
  document.getElementById('clockOffsetBadge').textContent = `偏移 +${{data.clock_offset_hours}}h`;

  const sel = document.getElementById('rewardUserSelect');
  const prev = sel.value;
  sel.innerHTML = data.users.map(u => `<option value="${{u.user.id}}">${{u.user.email}} (${{u.user.display_name}})</option>`).join('');
  if (prev) sel.value = prev;

  document.getElementById('usersTableArea').innerHTML = `<table>
    <thead><tr><th>帳號</th><th>訂閱方案 / 效期</th><th>綁定裝置 / 冷卻 / 額度</th><th>獎勵券</th><th>管理操作</th></tr></thead>
    <tbody>
      ${{data.users.map(item => {{
        const u = item.user, s = item.subscription, st = item.seat;
        const statusCls = !s ? 'b-muted' : (s.status==='active'?'b-ok':(s.status==='past_due'?'b-warn':'b-danger'));
        const cdH = st ? Math.ceil((st.transfer_cooldown_remaining_seconds || 0) / 3600) : 0;
        const rewHtml = item.rewards.length === 0 ? '<span style="color:var(--muted)">無</span>' : item.rewards.map(r =>
          `<div style="margin-bottom:4px;">+${{r.extend_days}}天 <span class="badge ${{r.status==='available'?'b-ok':(r.status==='claimed'?'b-muted':'b-danger')}}">${{r.status}}</span>` +
          (r.status === 'available' ? ` <button class="btn-sm btn-danger" onclick="revokeReward('${{r.id}}')">撤銷</button>` : '') + `</div>`
        ).join('');
        return `<tr>
          <td><strong>${{u.email}}</strong><br/><span style="color:var(--muted)">${{u.display_name}}</span></td>
          <td>${{s ? `${{s.plan==='annual'?'一年期':'三個月期'}} <span class="badge ${{statusCls}}">${{s.status}}</span><br/>到期：${{fmtTime(s.current_period_end)}}` : '無訂閱'}}</td>
          <td>${{st ? `裝置：<span class="mono">${{st.active_device_id || '尚未綁定'}}</span><br/>換機冷卻：${{cdH > 0 ? cdH + 'h' : '無'}} ｜ 緊急換機：${{st.emergency_transfer_remaining}}/1<br/>24h延長：${{st.grace_available ? '可用' : '冷卻中'}}` : '--'}}
              ${{st && st.revoked_at ? '<br/><span class="badge b-danger">席位已撤銷</span>' : ''}}</td>
          <td>${{rewHtml}}</td>
          <td><div class="row">
            ${{s && s.status==='active' ? `<button class="btn-sm btn-warn" onclick="setPastDue('${{u.id}}')">模擬扣款失敗</button>` : ''}}
            ${{st ? `<button class="btn-sm" onclick="resetCooldown('${{st.id}}')">解除冷卻</button>` : ''}}
            ${{st ? (st.revoked_at
              ? `<button class="btn-sm btn-ok" onclick="toggleRevoke('${{st.id}}', false)">恢復席位</button>`
              : `<button class="btn-sm btn-danger" onclick="toggleRevoke('${{st.id}}', true)">撤銷席位</button>`) : ''}}
          </div></td>
        </tr>`;
      }}).join('')}}
    </tbody>
  </table>`;

  document.getElementById('logsTableArea').innerHTML = `<table>
    <thead><tr><th>時間</th><th>等級</th><th>事件類型</th><th>裝置異動</th><th>細節</th></tr></thead>
    <tbody>
      ${{data.audit_logs.map(l => `<tr>
        <td class="mono">${{fmtTime(l.created_at)}}</td>
        <td><span class="badge ${{l.severity==='alert'?'b-danger':(l.severity==='warn'?'b-warn':'b-muted')}}">${{l.severity}}</span></td>
        <td class="mono">${{l.event_type}}</td>
        <td class="mono">${{l.old_device_id ? l.old_device_id + ' → ' : ''}}${{l.new_device_id || '--'}}</td>
        <td class="mono">${{JSON.stringify(l.detail)}}</td>
      </tr>`).join('')}}
    </tbody>
  </table>`;
}}

loadOverview();
</script>
</body>
</html>"""
