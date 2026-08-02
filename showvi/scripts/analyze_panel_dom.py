"""Quick DOM analysis to find the scrollable container in the voice popup."""
import time, json
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://jimeng.jianying.com/ai-tool/generate?type=digitalHuman&workspace=0"

def main():
    ud = str(Path.home() / ".jimeng_browser_cache")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=ud, headless=False,
            viewport={"width": 1440, "height": 900}, locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        
        print("Opening page...")
        try: page.goto(URL, timeout=20000)
        except: pass
        time.sleep(3)
        
        if "generate" not in page.url:
            print("Please login in the browser...")
            for i in range(300):
                time.sleep(1)
                if "generate" in page.url: break
            time.sleep(3)
        
        # Open voice panel
        page.evaluate("""() => {
            for (const el of document.querySelectorAll('div, span, p, button, a')) {
                if (el.textContent?.trim() === '数字人' && el.offsetParent) {
                    const r = el.getBoundingClientRect();
                    if (r.top > 500) { el.click(); return; }
                }
            }
        }""")
        time.sleep(2)
        page.evaluate("""() => {
            for (const el of document.querySelectorAll('div, span, p, button')) {
                if (el.textContent?.trim() === '音色' && el.offsetParent) {
                    const r = el.getBoundingClientRect();
                    if (r.top > 400 && r.width < 200) { el.click(); return; }
                }
            }
        }""")
        time.sleep(1.5)
        page.evaluate("""() => {
            for (const el of document.querySelectorAll('div, span, p, button, a')) {
                if (el.textContent?.trim() === '全部音色' && el.offsetParent) { el.click(); return; }
            }
        }""")
        time.sleep(2)
        
        # Analysis 1: elements at voice grid center
        print("\n=== elementsFromPoint(550, 420) ===")
        analysis1 = page.evaluate("""() => {
            const results = [];
            const els = document.elementsFromPoint(550, 420);
            for (const el of els) {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                results.push({
                    tag: el.tagName,
                    cls: String(el.className?.baseVal || el.className || '').substring(0, 80),
                    rect: {l: Math.round(r.left), t: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)},
                    scrollH: el.scrollHeight,
                    clientH: el.clientHeight,
                    overflow: style.overflow + '/' + style.overflowY,
                    canScroll: el.scrollHeight > el.clientHeight + 10,
                    textSnippet: el.textContent?.substring(0, 50).trim()
                });
            }
            return results;
        }""")
        for a in analysis1:
            marker = " ★ SCROLLABLE" if a['canScroll'] else ""
            print(f"  <{a['tag']}> cls={a['cls'][:50]}")
            print(f"    rect={a['rect']} scrollH={a['scrollH']} clientH={a['clientH']} overflow={a['overflow']}{marker}")
        
        # Analysis 2: find ALL scrollable elements in the area
        print("\n=== All scrollable elements in popup area ===")
        analysis2 = page.evaluate("""() => {
            const results = [];
            for (const el of document.querySelectorAll('*')) {
                if (el.scrollHeight <= el.clientHeight + 10) continue;
                if (el.clientHeight < 50) continue;
                const r = el.getBoundingClientRect();
                // Must overlap with the popup area (~310-790, 270-480)
                if (r.left > 900 || r.right < 200 || r.top > 600 || r.bottom < 200) continue;
                const style = window.getComputedStyle(el);
                results.push({
                    tag: el.tagName,
                    cls: String(el.className?.baseVal || el.className || '').substring(0, 80),
                    rect: {l: Math.round(r.left), t: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)},
                    scrollH: el.scrollHeight,
                    clientH: el.clientHeight,
                    overflow: style.overflow + '/' + style.overflowY,
                    hasVoiceText: el.textContent?.includes('直爽女大') || false,
                });
            }
            return results;
        }""")
        for a in analysis2:
            voice = " ← HAS_VOICES" if a['hasVoiceText'] else ""
            print(f"  <{a['tag']}> cls={a['cls'][:50]}")
            print(f"    rect={a['rect']} scrollH={a['scrollH']} clientH={a['clientH']} overflow={a['overflow']}{voice}")
        
        # Analysis 3: directly find the voice container
        print("\n=== Voice grid container hierarchy ===")
        analysis3 = page.evaluate("""() => {
            // Find "直爽女大" element
            for (const el of document.querySelectorAll('*')) {
                const raw = el.textContent?.trim().replace(/多情感/g,'').replace(/收藏/g,'').trim();
                if (raw === '直爽女大' && el.offsetParent) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 250 && r.height < 50) {
                        // Walk up the tree
                        const chain = [];
                        let p = el;
                        let depth = 0;
                        while (p && p !== document.body && depth < 15) {
                            const pr = p.getBoundingClientRect();
                            const style = window.getComputedStyle(p);
                            chain.push({
                                depth,
                                tag: p.tagName,
                                cls: String(p.className?.baseVal || p.className || '').substring(0, 80),
                                rect: {l: Math.round(pr.left), t: Math.round(pr.top), w: Math.round(pr.width), h: Math.round(pr.height)},
                                scrollH: p.scrollHeight,
                                clientH: p.clientHeight,
                                overflow: style.overflow + '/' + style.overflowY,
                                canScroll: p.scrollHeight > p.clientHeight + 10,
                            });
                            p = p.parentElement;
                            depth++;
                        }
                        return chain;
                    }
                }
            }
            return [];
        }""")
        for a in analysis3:
            indent = "  " * a['depth']
            marker = " ★ SCROLLABLE" if a['canScroll'] else ""
            print(f"  {indent}<{a['tag']}> cls={a['cls'][:50]}")
            print(f"  {indent}  rect={a['rect']} scrollH={a['scrollH']} clientH={a['clientH']} overflow={a['overflow']}{marker}")
        
        print("\nDone. Closing browser...")
        ctx.close()

if __name__ == "__main__":
    main()
