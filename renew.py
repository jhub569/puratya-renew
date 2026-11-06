#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MWS 自动续期脚本"""
import os, sys, json, time
from datetime import datetime
import requests
from notify import notify

BASE_URL = "https://cloud.m-ws.cc"
API = BASE_URL + "/api"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def http(path, method="GET", token=None, payload=None):
    headers = {"User-Agent": UA}
    if token: headers["Cookie"] = "__Host-mrtcloud_token=" + token
    if method != "GET": headers["Content-Type"] = "application/json"
    try:
        resp = requests.request(method, API + path, headers=headers, json=payload, timeout=30)
        return resp.status_code, resp.text
    except requests.RequestException as e: return 0, str(e)

def now_str(): return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

def collect(token):
    items = []
    for kind, path, key in [("Bot", "/bots", "bots"), ("Site", "/sites", "sites")]:
        status, body = http(path, token=token)
        if status != 200:
            print("[!] 获取 {} 失败: HTTP {} {}".format(path, status, body))
            items.append((kind, None, "<失败>", None)); continue
        try: data = json.loads(body)
        except: data = []
        lst = data if isinstance(data, list) else data.get(key, [])
        for obj in lst or []:
            oid = obj.get("id"); name = obj.get("name") or obj.get("username") or "id:{}".format(oid)
            rem = (obj.get("timer") or {}).get("remaining_hours")
            items.append((kind, oid, name, rem))
    return items

def init_browser(token):
    from DrissionPage import ChromiumPage, ChromiumOptions
    co = ChromiumOptions()
    for a in ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled", "--window-size=1280,800", "--disable-features=IsolateOrigins,site-per-process", "--disable-infobars", "--start-maximized"]:
        co.set_argument(a)
    co.set_user_agent(UA)
    page = ChromiumPage(co)
    page.get(BASE_URL)
    page.run_cdp("Network.setCookie", name="__Host-mrtcloud_token", value=token, domain="cloud.m-ws.cc", path="/", secure=True, httpOnly=True)
    page.get(BASE_URL); time.sleep(3)
    page.get(BASE_URL + "/bots"); time.sleep(3)
    return page

def renew_via_browser_fetch(page, kind, oid):
    page_url = "{}/{}".format(BASE_URL, "bots" if kind == "Bot" else "sites")
    try: page.get(page_url); time.sleep(1)
    except: pass
    api_path = "/api/bots/{}/renew".format(oid) if kind == "Bot" else "/api/sites/{}/renew".format(oid)
    js_script = r"""
    async () => {
        try {
            const resp = await fetch('%s', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json, text/plain, */*', 'Origin': '%s', 'Referer': '%s', 'X-Requested-With': 'XMLHttpRequest'},
                credentials: 'include'
            });
            const text = await resp.text();
            return { status: resp.status, body: text };
        } catch (e) { return { status: 0, body: e.toString() }; }
    }""" % (api_path, BASE_URL, page_url + "/")
    try:
        res = page.run_js(js_script)
        if res and isinstance(res, dict): return res.get("status", 0) == 200, res.get("status", 0), res.get("body", "")
        return False, 0, "JS 未返回结果"
    except Exception as e: return False, 500, str(e)

def renew_via_dom_click(page, kind, oid):
    detail_path = "{}/{}/{}".format(BASE_URL, "bots" if kind == "Bot" else "sites", oid)
    try: page.get(detail_path); time.sleep(3)
    except Exception as e: return False, 500, "无法访问: {}".format(e)
    selectors = ['text("续期")', 'text("延长")', 'text("Renew")', 'text("Extend")', 'text("延長")', 'css("[data-action*=renew]")', 'css("[data-action*=extend]")']
    for sel in selectors:
        try:
            btn = page.ele(sel, timeout=2)
            if btn:
                btn.click(); time.sleep(2)
                for cs in ['text("确认")', 'text("Confirm")', 'text("OK")', 'css(".ant-btn-primary")']:
                    try:
                        conf = page.ele(cs, timeout=1)
                        if conf: conf.click(); time.sleep(2); break
                    except: pass
                return True, 200, "DOM 成功"
        except: continue
    return False, 404, "未找到按钮"

def _report(level, title, content, details):
    try: notify(title, content, level=level, details=details)
    except Exception as e: print("::warning::通知失败: {}".format(e))

def main():
    token = os.environ.get("MWS_TOKEN", "").strip()
    if not token: print("[✗] MWS_TOKEN 未设置"); sys.exit(1)
    status, body = http("/auth/me", token=token)
    if status == 401: _report("failed", "⚠️ Token 已失效 ({})".format(now_str()), "请重新登录更新 Secret", None); sys.exit(1)
    if status != 200: print("[✗] 验证异常: HTTP {} {}".format(status, body)); sys.exit(1)
    try: who = json.loads(body).get("username")
    except: who = "?"
    print("[✓] 登录有效: {}".format(who))
    items = collect(token)
    if not items: _report("success", "MWS 续期 ({}) · 无对象".format(now_str()), "跳过", None); return
    page = None
    try: page = init_browser(token)
    except Exception as e: print("[!] 浏览器启动失败: {}".format(e))
    lines, failed, detail_items = [], 0, []
    for kind, oid, name, rem in items:
        if oid is None:
            lines.append("  · {} {}：列表获取失败".format(kind, name))
            detail_items.append({"id": "unknown", "name": name, "status": "failed"}); failed += 1; continue
        ok, status, resp_body = False, 0, ""
        if page:
            ok, status, resp_body = renew_via_browser_fetch(page, kind, oid)
            if not ok:
                print("[!] fetch 失败，尝试 DOM 点击...")
                ok, status, resp_body = renew_via_dom_click(page, kind, oid)
        else: ok, status, resp_body = False, 500, "浏览器未初始化"
        rem_txt = "，续期前剩 {}h".format(int(rem)) if rem is not None else ""
        if ok:
            lines.append("  · {} {}：续期成功{}".format(kind, name, rem_txt))
            detail_items.append({"id": str(oid), "name": name, "status": "success"})
            print("[✓] {} {} 成功{}".format(kind, name, rem_txt))
        else:
            lines.append("  · {} {}：续期失败 HTTP {} {}".format(kind, name, status, resp_body.strip()))
            detail_items.append({"id": str(oid), "name": name, "status": "failed", "error": "HTTP {}".format(status)})
            failed += 1
            print("[✗] {} {} 失败 HTTP {}".format(kind, name, status))
    if page:
        try: page.quit()
        except: pass
    total = len(items); success = total - failed
    level = "success" if failed == 0 else "partial"
    word = "完成" if failed == 0 else "部分失败"
    title = "MWS 续期 ({}) · {}".format(now_str(), word)
    content = "
".join(lines)
    print("
" + title + "
" + content)
    _report(level, title, content, {"total": total, "success": success, "failed": failed, "details": detail_items})
    if failed: sys.exit(1)

if __name__ == "__main__": main()
