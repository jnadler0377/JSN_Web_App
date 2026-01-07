from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

PORTAL_URL = "https://courtrecords.mypinellasclerk.gov/"

DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug")
os.makedirs(DEBUG_DIR, exist_ok=True)
LOG_PATH = os.path.join(DEBUG_DIR, "pinellas_scraper.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pinellas-foreclosure")


def human_delay(max_sec: int, *, min_sec: float = 0.5) -> None:
    if max_sec <= 0:
        return
    time.sleep(random.uniform(min_sec, max(max_sec, min_sec)))


def _safe_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _fmt_mdy(dt: datetime) -> str:
    return dt.strftime("%m/%d/%Y")


def _set_date_input(page, selector: str, value: str) -> None:
    loc = page.locator(selector).first
    loc.click()
    loc.fill("")
    loc.fill(value)
    try:
        loc.evaluate(
            "(el, val) => { el.value = val; "
            "el.dispatchEvent(new Event('input', { bubbles: true })); "
            "el.dispatchEvent(new Event('change', { bubbles: true })); "
            "el.dispatchEvent(new Event('blur', { bubbles: true })); }",
            value,
        )
    except Exception:
        pass


def _maybe_accept_disclaimer(page, humanize_max: int) -> None:
    buttons = [
        "button:has-text('Accept')",
        "button:has-text('I Accept')",
        "button:has-text('Agree')",
        "button:has-text('I Agree')",
        "button:has-text('Continue')",
        "a:has-text('Accept')",
        "a:has-text('I Accept')",
        "a:has-text('Agree')",
        "a:has-text('I Agree')",
        "a:has-text('Continue')",
        "input[type='submit'][value*='Accept']",
        "input[type='button'][value*='Accept']",
        ".btn:has-text('Accept')",
        ".btn:has-text('Agree')",
    ]
    for sel in buttons:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                log.info(f"Found disclaimer button with selector: {sel}")
                btn.first.click()
                human_delay(min(2, humanize_max))
                break
        except Exception:
            continue


def _open_case_tab(page, humanize_max: int) -> None:
    # Try multiple selectors for the Case tab
    tab_selectors = [
        "a[href='#case']",
        "a:has-text('Case')",
        "li a:has-text('Case')",
        ".nav-tabs a:has-text('Case')",
        ".nav a:has-text('Case')",
        "#caseTabs a:has-text('Case')",
        "button:has-text('Case')",
    ]
    
    for sel in tab_selectors:
        try:
            tab = page.locator(sel).first
            if tab.count() > 0 and tab.is_visible():
                log.info(f"Found Case tab with selector: {sel}")
                tab.click()
                human_delay(min(2, humanize_max))
                return
        except Exception as e:
            log.debug(f"Tab selector {sel} failed: {e}")
            continue
    
    # If no tab found, the page might already be on Case search
    log.warning("Case tab not found - page may already be on Case search")


def _set_case_type(page, humanize_max: int) -> None:
    container = page.locator("#case")
    if container.count() == 0:
        container = page.locator("body")

    # Try multiple selectors for the case type dropdown
    dropdown_btn_selectors = [
        "button.multiselect.dropdown-toggle",
        "button.multiselect",
        ".multiselect.dropdown-toggle",
        "select#caseTypeSelect",
        "#caseType",
        "button:has-text('Select Case Type')",
        "button:has-text('Case Type')",
    ]
    
    btn = None
    for sel in dropdown_btn_selectors:
        try:
            btn_loc = container.locator(sel).first
            if btn_loc.count() > 0 and btn_loc.is_visible():
                btn = btn_loc
                log.info(f"Found case type dropdown with selector: {sel}")
                break
        except Exception:
            continue
    
    if btn is None:
        log.warning("Case type dropdown not found - may not be required")
        return
        
    btn.click()
    human_delay(min(1, humanize_max))

    dropdown = container.locator("ul.multiselect-container").first
    if dropdown.count() == 0:
        dropdown = page.locator("ul.multiselect-container").first
    
    if dropdown.count() == 0:
        dropdown = page.locator(".dropdown-menu:visible").first

    if dropdown.count() == 0:
        log.warning("Dropdown menu not found after clicking button")
        return

    try:
        select_all = dropdown.locator("input[type='checkbox'][value='multiselect-all']")
        if select_all.count() > 0 and select_all.first.is_checked():
            select_all.first.click()
            human_delay(min(1, humanize_max))
    except Exception:
        pass

    try:
        checked = dropdown.locator("input[type='checkbox']:checked")
        for i in range(checked.count()):
            checked.nth(i).click()
            human_delay(min(0.2, humanize_max))
    except Exception:
        pass

    # Try to find and select "Real Property/Mortgage Foreclosure"
    selected = False
    foreclosure_selectors = [
        "input[type='checkbox'][value='Real Property/Mortgage Foreclosure']",
        "input[type='checkbox'][value*='Foreclosure']",
        "input[type='checkbox'][value*='foreclosure']",
    ]
    
    for sel in foreclosure_selectors:
        target = dropdown.locator(sel)
        if target.count() > 0:
            if not target.first.is_checked():
                target.first.click()
            selected = True
            human_delay(min(1, humanize_max))
            break
    
    # Try by label text if not found by checkbox value
    if not selected:
        label_texts = [
            "Real Property/Mortgage Foreclosure",
            "Mortgage Foreclosure",
            "Foreclosure",
        ]
        
        for text in label_texts:
            label = dropdown.locator(f"label:has-text('{text}')")
            if label.count() > 0:
                label.first.click()
                selected = True
                human_delay(min(1, humanize_max))
                break
    
    if not selected:
        log.warning("Foreclosure case type option not found in dropdown")
    
    # IMPORTANT: Close the dropdown by clicking the button again or pressing Escape
    try:
        # Try clicking the dropdown button again to close it
        btn.click()
        human_delay(min(0.5, humanize_max))
    except Exception:
        pass
    
    try:
        # Also press Escape to make sure it's closed
        page.keyboard.press("Escape")
        human_delay(min(0.5, humanize_max))
    except Exception:
        pass
    
    # Click somewhere neutral to ensure dropdown is closed
    try:
        page.locator("#case").click(position={"x": 10, "y": 10})
        human_delay(min(0.5, humanize_max))
    except Exception:
        pass
    
    log.info("Case type selected and dropdown closed")


def _submit_case_search(page, humanize_max: int) -> None:
    # First, close any open dropdowns by clicking elsewhere
    try:
        page.locator("body").click(position={"x": 10, "y": 10})
        human_delay(0.5)
    except Exception:
        pass
    
    container = page.locator("#case")
    if container.count() == 0:
        container = page.locator("body")

    # Extended list of possible selectors for the search/submit button
    # Be more specific to avoid clicking dropdown items
    selectors = [
        "#caseSearch",
        "#caseSearchBtn",
        "#searchButton",
        "#btnSearch",
        "#submit",
        "button#caseSearch",
        "button#caseSearchBtn",
        "button#searchButton",
        "button#btnSearch",
        # Look for buttons with search-related classes
        "button.btn-primary[type='submit']",
        "button.btn-search",
        "button.search-btn",
        # Text-based but exclude dropdown items
        "#case > button:has-text('Search')",
        "#case > div > button:has-text('Search')",
        "#case button.btn-primary:has-text('Search')",
        "#case button.btn:has-text('Search'):not(.multiselect)",
        "form button:has-text('Search')",
        "form button:has-text('Submit')",
        "form input[type='submit']",
        # More generic submit buttons
        "input[type='submit'][value='Search']",
        "input[type='submit'][value='Submit']",
        "input[type='submit']",
        "input[type='button'][value='Search']",
        "input[type='button'][value='Submit']",
    ]
    
    for sel in selectors:
        try:
            btn = container.locator(sel)
            if btn.count() > 0:
                # Verify this isn't a dropdown item
                btn_text = ""
                try:
                    btn_text = btn.first.inner_text().strip().lower()
                except:
                    pass
                
                # Skip if it looks like a dropdown option
                if "foreclosure" in btn_text or "property" in btn_text:
                    log.debug(f"Skipping dropdown item: {btn_text}")
                    continue
                    
                log.info(f"Found search button with selector: {sel}")
                btn.first.scroll_into_view_if_needed()
                btn.first.click()
                human_delay(min(2, humanize_max))
                return
        except Exception as e:
            log.debug(f"Selector {sel} failed: {e}")
            continue

    # Fallback: look for any button that looks like a search/submit button
    try:
        # Close dropdown again just in case
        page.keyboard.press("Escape")
        human_delay(0.5)
        
        all_buttons = page.locator("#case button:visible, #case input[type='submit']:visible")
        log.info(f"Fallback: Found {all_buttons.count()} visible buttons in #case")
        
        for i in range(all_buttons.count()):
            btn = all_buttons.nth(i)
            try:
                btn_text = btn.inner_text().strip() if btn.evaluate("el => el.tagName") == "BUTTON" else (btn.get_attribute("value") or "")
                btn_class = btn.get_attribute("class") or ""
                log.info(f"  Button {i}: text='{btn_text}', class='{btn_class}'")
                
                # Skip dropdown-related buttons
                if "multiselect" in btn_class.lower() or "dropdown" in btn_class.lower():
                    continue
                if "foreclosure" in btn_text.lower() or "property" in btn_text.lower():
                    continue
                    
                # This might be the search button
                if "search" in btn_text.lower() or "submit" in btn_text.lower() or btn_text == "":
                    log.info(f"  -> Clicking button {i}")
                    btn.click()
                    human_delay(min(2, humanize_max))
                    return
            except Exception as e:
                log.debug(f"  Button {i} inspection failed: {e}")
                continue
    except Exception as e:
        log.error(f"Fallback button search failed: {e}")

    # Save debug screenshot
    try:
        debug_path = os.path.join(DEBUG_DIR, "submit_button_not_found.png")
        page.screenshot(path=debug_path)
        log.info(f"Debug screenshot saved to: {debug_path}")
        
        # Also save the HTML for inspection
        html_path = os.path.join(DEBUG_DIR, "submit_button_not_found.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
        log.info(f"Debug HTML saved to: {html_path}")
    except Exception:
        pass

    raise RuntimeError("Could not locate submit/search button on Case tab.")


def _find_results_table(page):
    case_table = page.locator("table#caseList")
    if case_table.count() > 0:
        headers = case_table.first.locator("thead tr th")
        header_texts = [_safe_text(headers.nth(j).inner_text()) for j in range(headers.count())]
        return case_table.first, header_texts

    tables = page.locator("table")
    for i in range(tables.count()):
        table = tables.nth(i)
        headers = table.locator("thead tr th")
        header_texts = [_safe_text(headers.nth(j).inner_text()) for j in range(headers.count())]
        if any("case" in h.lower() for h in header_texts):
            return table, header_texts

    if tables.count() > 0:
        table = tables.first
        headers = table.locator("thead tr th")
        header_texts = [_safe_text(headers.nth(j).inner_text()) for j in range(headers.count())]
        return table, header_texts

    return None, []


def _header_index(headers: List[str], variants: List[str]) -> Optional[int]:
    for idx, h in enumerate(headers):
        hl = h.lower()
        for v in variants:
            if v.lower() in hl:
                return idx
    return None


def _split_names(text: str) -> List[str]:
    raw = [p.strip() for p in text.replace("\r", "\n").split("\n")]
    out: List[str] = []
    for item in raw:
        if not item:
            continue
        if "|" in item:
            out.extend([p.strip() for p in item.split("|") if p.strip()])
        else:
            out.append(item)

    seen = set()
    unique = []
    for name in out:
        if "unknown" in name.lower():
            continue
        if name not in seen:
            unique.append(name)
            seen.add(name)
    return unique


def _fallback_defendants_from_style(style: str) -> List[str]:
    if not style:
        return []
    lowered = style.lower()
    for token in [" vs ", " v. ", " v ", " vs. "]:
        if token in lowered:
            parts = style.split(token, 1)
            if len(parts) == 2:
                return _split_names(parts[1])
    return []


def _extract_detail_defendants_from_page(page) -> Tuple[List[str], List[str]]:
    rows = page.locator("tr.ptr")
    best_rows: List[Tuple[str, str]] = []
    for i in range(rows.count()):
        row = rows.nth(i)
        cells = row.locator("td")
        if cells.count() < 3:
            continue
        role = _safe_text(cells.nth(1).inner_text())
        if "defendant" not in role.lower():
            continue
        name = _safe_text(cells.nth(0).inner_text())
        if "unknown" in name.lower():
            continue
        addr = _safe_text(cells.nth(2).inner_text())
        if name or addr:
            best_rows.append((name, addr))

    defendants = [n for n, _ in best_rows if n]
    addresses = [a for _, a in best_rows if a]
    return defendants, addresses


def _extract_rows_with_details(page, humanize_max: int) -> List[Dict[str, Any]]:
    table, headers = _find_results_table(page)
    if not table:
        return []

    rows = table.locator("tbody tr")
    if rows.count() == 0:
        rows = table.locator("tr")

    case_idx = _header_index(headers, ["case#", "case #", "case number", "case no", "case"])
    style_idx = _header_index(headers, ["style", "case name", "case style"])
    filed_idx = _header_index(headers, ["filed", "filing date", "date filed"])
    party_idx = _header_index(headers, ["defendant", "party", "parties", "name"])

    results: List[Dict[str, Any]] = []
    for i in range(rows.count()):
        row = rows.nth(i)
        cells = row.locator("td")
        if cells.count() == 0:
            continue

        def cell_text(idx: Optional[int]) -> str:
            if idx is None or idx < 0 or idx >= cells.count():
                return ""
            return _safe_text(cells.nth(idx).inner_text())

        def case_link_locator(idx: Optional[int]):
            if idx is None or idx < 0 or idx >= cells.count():
                return None
            cell = cells.nth(idx)
            link = cell.locator("a.caseLink").first
            if link.count() == 0:
                link = cell.locator("a").first
            if link.count() == 0:
                return None
            return link

        link_loc = case_link_locator(case_idx)
        case_no = _safe_text(link_loc.inner_text()) if link_loc else cell_text(case_idx)
        case_name = cell_text(style_idx)
        filed_dt = cell_text(filed_idx)
        parties = cell_text(party_idx)

        defendants = _split_names(parties) if parties else _fallback_defendants_from_style(case_name)
        addresses: List[str] = []

        if link_loc:
            try:
                with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                    link_loc.click()
                page.wait_for_load_state("networkidle")
                human_delay(min(2, humanize_max))
                d_names, d_addrs = _extract_detail_defendants_from_page(page)
                if d_names:
                    defendants = d_names
                if d_addrs:
                    addresses = d_addrs
                page.go_back(wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle")
                human_delay(min(1, humanize_max))
            except Exception:
                pass

        if not (case_no or case_name or filed_dt):
            continue

        results.append(
            {
                "Case Name": case_name,
                "Case #": case_no,
                "Filing Date": filed_dt,
                "Defendants": defendants,
                "Defendant Addresses": addresses,
            }
        )

    return results


def _write_csv(csv_path: str, results: List[Dict[str, Any]]) -> None:
    max_defs = 0
    max_addrs = 0
    for r in results:
        max_defs = max(max_defs, len(r.get("Defendants", [])))
        max_addrs = max(max_addrs, len(r.get("Defendant Addresses", [])))

    columns = ["Case Name", "Case #", "Filing Date"] + [f"Defendant {i}" for i in range(1, max_defs + 1)] + [
        f"Defendant Address {i}" for i in range(1, max_addrs + 1)
    ]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in results:
            row = {"Case Name": r.get("Case Name", ""), "Case #": r.get("Case #", ""), "Filing Date": r.get("Filing Date", "")}
            defs = r.get("Defendants", [])
            addrs = r.get("Defendant Addresses", [])
            for i in range(max_defs):
                row[f"Defendant {i+1}"] = defs[i] if i < len(defs) else ""
            for i in range(max_addrs):
                row[f"Defendant Address {i+1}"] = addrs[i] if i < len(addrs) else ""
            writer.writerow(row)


def _resolve_headless(args) -> bool:
    # Default headless True (server safe)
    if args.headed:
        return False
    if args.headless:
        return True
    return True


def parse_args():
    p = argparse.ArgumentParser(description="Pinellas foreclosure scraper")

    # Unified mode flags (match Pasco)
    p.add_argument("--headed", action="store_true", help="Run with visible browser (requires X/desktop)")
    p.add_argument("--headless", action="store_true", help="Force headless mode (default on servers)")
    p.add_argument("--debug", action="store_true", help="Enable debug mode: headed browser, screenshots, slower delays")

    p.add_argument("--trace", action="store_true", help="Record Playwright trace")
    p.add_argument("--screenshot", action="store_true", help="Save a screenshot on failures")
    p.add_argument("--save-html", action="store_true", help="Save page HTML on failures")
    p.add_argument("--humanize-max", type=int, default=5, help="Max seconds for random pauses")
    p.add_argument("--since-days", type=int, default=0, help="Only include filings within the last N days")
    p.add_argument("--max-records", type=int, default=0, help="Stop after N records (0 = no limit)")
    p.add_argument("--out", type=str, default="", help="Write harvested CSV to this exact path")
    
    args = p.parse_args()
    
    # Debug mode enables headed + screenshots + save-html
    if args.debug:
        args.headed = True
        args.screenshot = True
        args.save_html = True
        log.info("Debug mode enabled: headed browser, screenshots on failure")
    
    return args


def _dump_artifacts(page, context, *, screenshot: bool, save_html: bool, trace: bool) -> None:
    try:
        if screenshot:
            page.screenshot(path=os.path.join(DEBUG_DIR, "pinellas_error.png"), full_page=True)
    except Exception:
        pass
    try:
        if save_html:
            html = page.content()
            with open(os.path.join(DEBUG_DIR, "pinellas_error.html"), "w", encoding="utf-8") as f:
                f.write(html)
    except Exception:
        pass
    try:
        if trace:
            context.tracing.stop(path=os.path.join(DEBUG_DIR, "pinellas_trace.zip"))
    except Exception:
        pass


def main():
    args = parse_args()
    csv_path = args.out.strip() or os.path.join(os.path.dirname(__file__), "output", "pinellas_foreclosures.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    headless = _resolve_headless(args)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        if args.trace:
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()

        try:
            log.info("Opening Pinellas clerk site...")
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_load_state("networkidle")
            human_delay(min(2, args.humanize_max))

            _maybe_accept_disclaimer(page, args.humanize_max)
            _open_case_tab(page, args.humanize_max)

            since_days = max(0, int(args.since_days))
            if since_days > 0:
                date_to = datetime.now()
                date_from = date_to - timedelta(days=since_days)
                
                # Try multiple date input selectors
                date_from_selectors = ["#DateFrom", "#dateFrom", "#fromDate", "input[name='DateFrom']", "input[name='dateFrom']"]
                date_to_selectors = ["#DateTo", "#dateTo", "#toDate", "input[name='DateTo']", "input[name='dateTo']"]
                
                for sel in date_from_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            _set_date_input(page, sel, _fmt_mdy(date_from))
                            log.info(f"Set date from using selector: {sel}")
                            break
                    except Exception:
                        continue
                        
                for sel in date_to_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            _set_date_input(page, sel, _fmt_mdy(date_to))
                            log.info(f"Set date to using selector: {sel}")
                            break
                    except Exception:
                        continue
            else:
                try:
                    page.locator("#DateFrom").fill("")
                    page.locator("#DateTo").fill("")
                except Exception:
                    pass

            human_delay(min(2, args.humanize_max))
            _set_case_type(page, args.humanize_max)
            human_delay(min(1, args.humanize_max))
            
            # Save debug screenshot before searching
            try:
                debug_path = os.path.join(DEBUG_DIR, "before_search.png")
                page.screenshot(path=debug_path)
                log.info(f"Pre-search screenshot saved to: {debug_path}")
            except Exception:
                pass

            _submit_case_search(page, args.humanize_max)
            page.wait_for_load_state("networkidle")

            try:
                page.wait_for_selector("table tbody tr", timeout=30000)
            except PlaywrightTimeout:
                log.warning("Timed out waiting for results table.")

            results = _extract_rows_with_details(page, args.humanize_max)
            if args.max_records and len(results) > args.max_records:
                results = results[: args.max_records]

            _write_csv(csv_path, results)
            log.info("Saved %d rows -> %s", len(results), csv_path)

        except Exception as exc:
            log.exception("Fatal error during Pinellas scrape: %s", exc)
            _dump_artifacts(page, context, screenshot=args.screenshot, save_html=args.save_html, trace=args.trace)
            raise
        finally:
            try:
                if args.trace:
                    _dump_artifacts(page, context, screenshot=False, save_html=False, trace=True)
                context.close()
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("EXIT WITH ERROR: %s", exc)
        if "submit" in str(exc).lower() or "button" in str(exc).lower():
            log.error("TIP: The website may have changed. Check debug files in: %s", DEBUG_DIR)
            log.error("TIP: Run with --debug flag to see the browser: python pinellas_foreclosure_scraper.py --debug")
        sys.exit(1)
    log.info("DONE")
