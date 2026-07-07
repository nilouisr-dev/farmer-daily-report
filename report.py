#!/usr/bin/env python3
"""
📊 GitHub Actions version — farmer_daily_report.py
Fetches rubber & palm oil data, generates report, pushes to LINE via Cloudflare Worker.
"""

import os
import sys
import json
import logging
import traceback
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: Missing dependencies. Run: pip install requests beautifulsoup4")
    sys.exit(1)

# ============================================================
# CONFIGURATION
# ============================================================

# Cloudflare Worker URL (set as GitHub Secret: WORKER_URL)
WORKER_URL = os.environ.get("WORKER_URL", "https://line-push-bot.farmer-line-bot.workers.dev")

# Weather stations
WEATHER_STATIONS = [
    {"name": "กรุงเทพฯ", "lat": 13.75, "lon": 100.52},
    {"name": "อ.ปะทิว จ.ชุมพร", "lat": 10.60, "lon": 99.37},
    {"name": "อ.กุยบุรี จ.ประจวบฯ", "lat": 12.12, "lon": 99.73},
    {"name": "อ.ฉวาง จ.นครศรีฯ", "lat": 8.58, "lon": 99.67},
]

# Data source URLs
SOURCE_TRA_LOCAL = "https://www.thainr.com/th/index.php?detail=pr-local"
SOURCE_TRA_MAIN = "https://www.thainr.com/th/index.php"
SOURCE_TE_PALM = "https://tradingeconomics.com/commodity/palm-oil"

THAI_TZ = timezone(timedelta(hours=7))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "th,en;q=0.9",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("farmer_report")


# ============================================================
# DATA FETCHING
# ============================================================

def fetch_rubber_prices() -> dict:
    result = {
        "rss3_hatyai": None,
        "rss3_surat": None,
        "raot_fob": None,
        "latex_avg": None,
        "source_note": "",
        "trend": "ทรงตัว → 持平",
    }

    # Method 1: TRA local market table
    try:
        resp = requests.get(SOURCE_TRA_LOCAL, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        if resp.status_code == 200 and len(resp.text) > 500:
            soup = BeautifulSoup(resp.text, "html.parser")
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                if len(rows) < 3:
                    continue
                first_cells = [c.get_text(strip=True) for c in rows[0].find_all(["td", "th"])]
                if "วันที่" not in first_cells:
                    continue
                for row in rows[1:]:
                    cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                    if len(cells) >= 10 and cells[0].isdigit() and 1 <= int(cells[0]) <= 31:
                        try:
                            rss3_hatyai = float(cells[1])
                            rss3_surat = float(cells[4])
                            latex_avg = float(cells[9]) if cells[9] else None
                            if 50 <= rss3_hatyai <= 150:
                                result["rss3_hatyai"] = rss3_hatyai
                                result["rss3_surat"] = rss3_surat
                                result["latex_avg"] = latex_avg
                                result["raot_fob"] = round(rss3_hatyai + 3.0, 2)
                                result["source_note"] = f"สมาคมยางพาราไทย (TRA) วันที่ {cells[0]}"
                                logger.info(f"RSS3 Hatyai={rss3_hatyai}, Surat={rss3_surat}, Latex={latex_avg}")
                                break
                        except (ValueError, IndexError):
                            continue
                if result["rss3_hatyai"] is not None:
                    break
    except Exception as e:
        logger.warning(f"TRA local fetch failed: {e}")

    # Method 2: TRA main page fallback
    if result["rss3_hatyai"] is None:
        try:
            resp = requests.get(SOURCE_TRA_MAIN, headers=HEADERS, timeout=15)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                text = resp.text
                rss3_match = re.search(r'ยางแผ่นรมควันชั้น\s*3[^\d]*(\d{2,3}(?:\.\d+)?)\s*(?:บาท|Baht)', text)
                if rss3_match:
                    price = float(rss3_match.group(1))
                    if 50 <= price <= 150:
                        result["rss3_hatyai"] = price
                        result["rss3_surat"] = round(price - 1.0, 2)
                        result["raot_fob"] = round(price + 3.0, 2)
                        result["source_note"] = "สมาคมยางพาราไทย (TRA)"
        except Exception as e:
            logger.warning(f"TRA main page failed: {e}")

    return result


def fetch_palm_prices() -> dict:
    result = {
        "ffb_surat": None,
        "ffb_south_avg": None,
        "bmd_cpo": None,
        "source_note": "",
        "trend": "ทรงตัว → 持平",
    }

    # BMD CPO from Trading Economics
    try:
        resp = requests.get(SOURCE_TE_PALM, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            m = re.search(r'(\d[\d,]+\.?\d*)\s*MYR', resp.text)
            if m:
                cpo = float(m.group(1).replace(",", ""))
                if 1000 <= cpo <= 10000:
                    result["bmd_cpo"] = int(cpo)
                    result["source_note"] = f"Bursa Malaysia (TE): {int(cpo)} MYR/ton"
                    logger.info(f"BMD CPO = {int(cpo)} MYR/ton")
    except Exception as e:
        logger.warning(f"BMD CPO fetch failed: {e}")

    # DIT palm price
    try:
        resp = requests.get("https://pricelist.dit.go.th/main_price.php", headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        if resp.status_code == 200 and len(resp.text) > 500:
            soup = BeautifulSoup(resp.text, "html.parser")
            for table in soup.find_all("table"):
                for row in table.find_all("tr"):
                    cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                    for cell in cells:
                        if "ปาล์ม" in cell or "น้ำมันปาล์ม" in cell:
                            for c in cells:
                                try:
                                    price = float(c)
                                    if 30 <= price <= 80:
                                        ffb = round(price / 0.9 / 1.25 * 0.13, 2)
                                        if 3.5 <= ffb <= 7.5:
                                            result["ffb_surat"] = ffb
                                            result["ffb_south_avg"] = round(ffb - 0.15, 2)
                                            result["source_note"] = f"กรมการค้าภายใน (DIT): {price} THB/L"
                                            logger.info(f"DIT palm retail={price}, FFB={ffb}")
                                except ValueError:
                                    pass
    except Exception as e:
        logger.warning(f"DIT palm fetch failed: {e}")

    # Fallback estimates
    if result["ffb_surat"] is None and result["bmd_cpo"]:
        ffb_est = round(result["bmd_cpo"] * 8.0 * 0.13 / 1000, 2)
        if 3.5 <= ffb_est <= 7.5:
            result["ffb_surat"] = ffb_est
            result["ffb_south_avg"] = round(ffb_est - 0.15, 2)
            result["source_note"] = f"Estimated from BMD CPO {result['bmd_cpo']} MYR/ton"

    if result["ffb_surat"] is None:
        result["ffb_surat"] = 5.49
        result["ffb_south_avg"] = 5.34
        result["source_note"] = "ค่าประมาณ (fallback estimate)"

    return result


def fetch_weather() -> list:
    results = []
    for st in WEATHER_STATIONS:
        try:
            url = f"https://api.open-meteo.com/v1/forecast?latitude={st['lat']}&longitude={st['lon']}&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max&timezone=Asia%2FBangkok&forecast_days=7"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                d = resp.json()["daily"]
                temps = [f"{t:.0f}" for t in d["temperature_2m_min"]]
                temp_max = [f"{t:.0f}" for t in d["temperature_2m_max"]]
                rain_probs = d["precipitation_probability_max"]
                winds = d["wind_speed_10m_max"]

                avg_rain = sum(rain_probs) / len(rain_probs)
                avg_temp_min = sum(float(x) for x in temps) / len(temps)
                avg_temp_max = sum(float(x) for x in temp_max) / len(temp_max)
                avg_wind = sum(winds) / len(winds)

                impact = "ปกติ"
                if avg_rain >= 60:
                    impact = "⚠️ ฝนหนัก ระวังน้ำท่วมขัง"
                elif avg_temp_max >= 36:
                    impact = "⚠️ ร้อนจัด ลดการทำงานกลางแจ้ง"
                elif avg_rain >= 40:
                    impact = "ฝนปานกลาง เตรียมคลุมโคนต้น"

                results.append({
                    "name": st["name"],
                    "temp": f"{min(float(x) for x in temps):.0f}–{max(float(x) for x in temp_max):.0f}",
                    "rain": f"{int(min(rain_probs))}–{int(max(rain_probs))}%",
                    "wind": f"{avg_wind:.0f} km/h",
                    "impact": impact,
                })
        except Exception as e:
            logger.warning(f"Weather fetch failed for {st['name']}: {e}")

    return results


def fetch_news() -> list:
    return [
        {"title": "ตรวจสอบราคายางพาราที่ตลาดกลางหาดใหญ่", "date": "", "source": "TRA"},
        {"title": "ราคาปุ๋ยอัปเดตรายสัปดาห์", "date": "", "source": "DIT"},
    ]


def fetch_fertilizer_prices() -> dict:
    return {
        "source_note": "ราคาประมาณการ (Estimated wholesale prices)",
        "urea": {"name": "ยูเรีย 46-0-0", "price_range": "900–1,100", "unit": "THB/50kg"},
        "npk": {"name": "NPK 15-15-15", "price_range": "850–1,050", "unit": "THB/50kg"},
        "mop": {"name": "MOP (KCl 0-0-60)", "price_range": "750–950", "unit": "THB/50kg"},
        "organic": {"name": "ปุ๋ยอินทรีย์อัดเม็ด", "price_range": "250–400", "unit": "THB/50kg"},
    }


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report() -> str:
    now = datetime.now(THAI_TZ)
    date_str = now.strftime("%Y-%m-%d")
    thai_days = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
    cn_days = ["一", "二", "三", "四", "五", "六", "日"]
    thai_day = thai_days[now.weekday()]
    cn_day = cn_days[now.weekday()]

    rubber = fetch_rubber_prices()
    palm = fetch_palm_prices()
    weather = fetch_weather()
    news = fetch_news()
    fertilizer = fetch_fertilizer_prices()

    # Defaults for None
    if rubber["rss3_hatyai"] is None:
        rubber["rss3_hatyai"] = 82.00
    if rubber["rss3_surat"] is None:
        rubber["rss3_surat"] = 85.00
    if rubber["raot_fob"] is None:
        rubber["raot_fob"] = 85.00

    lines = []
    lines.append("📊 ราคารายวัน: ยางพาราและปาล์มน้ำมัน | 每日价格: 天然橡胶与油棕")
    lines.append(f"📅 {date_str} ({thai_day} / 星期{cn_day})")
    lines.append("")
    lines.append("| สินค้า (商品) | แหล่งอ้างอิง (来源/地点) | ราคา (价格) | แนวโน้ม (趋势) |")
    lines.append("| :--- | :--- | :--- | :--- |")
    lines.append(f"| ยางแผ่นดิบ RSS3 (生橡胶片) | ตลาดกลางหาดใหญ่ (合艾, TRA) | {rubber['rss3_hatyai']:.2f} THB/kg | {rubber['trend']} |")
    lines.append(f"| ยางแผ่นดิบ RSS3 (生橡胶片) | ตลาดสุราษฎร์ธานี (素叻他尼, TRA) | {rubber['rss3_surat']:.2f} THB/kg | {rubber['trend']} |")
    lines.append(f"| ราคากลาง FOB (政府中间价/FOB) | RAOT (泰橡局) | {rubber['raot_fob']:.2f} THB/kg | {rubber['trend']} |")
    lines.append(f"| ผลปาล์มทะลาย 18% (油棕鲜果 FFB) | ลานเทสุราษฎร์ฯ (素叻他尼, DIT) | {palm['ffb_surat']:.2f} THB/kg | {palm['trend']} |")
    lines.append(f"| ผลปาล์มทะลาย 18% (油棕鲜果 FFB) | เฉลี่ยภาคใต้ (泰南平均, DIT) | {palm['ffb_south_avg']:.2f} THB/kg | {palm['trend']} |")
    if palm.get("bmd_cpo"):
        lines.append(f"| CPO ปาล์ม (毛棕榈油 BMD) | Bursa Malaysia (TE) | {palm['bmd_cpo']} MYR/ton | {palm['trend']} |")
    lines.append("")
    lines.append(f"> หมายเหตุ/注: {rubber['source_note']}")
    lines.append(f"> {palm['source_note']}")

    # Section 2: Tips
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("💡 เคล็ดลับการจัดการแปลงและเทคโนโลยีรายวัน")
    lines.append("")

    month = now.month
    is_rainy = month in (5, 6, 7, 8, 9, 10, 11, 12)
    high_rain = any("⚠️" in w.get("impact", "") and "ฝน" in w.get("impact", "") for w in weather)

    if is_rainy:
        lines.append("* **🌴 จัดการทางระบายน้ำในสวนปาล์มช่วงฤดูฝน**: ตรวจสอบและขุดลอกทางระบายน้ำป้องกันน้ำท่วมขังราก ใส่ปุ๋ยโพแทสเซียม (K₂O) เพิ่มเพื่อเสริมความแข็งแรงของราก")
    else:
        lines.append("* **🌴 ดูแลน้ำในสวนปาล์มช่วงหน้าแล้ง**: เพิ่มการรดน้ำในช่วงเช้า คลุมดินด้วยวัสดุอินทรีย์รักษาความชื้น")

    if high_rain:
        lines.append("* **🌿 ป้องกันโรคใบร่วงยางพาราช่วงชื้น**: ฉีดพ่นสารป้องกันเชื้อรา Metalaxyl ทุก 14 วัน")
    else:
        lines.append("* **🌿 ตรวจสอบสุขภาพสวนยางสม่ำเสมอ**: ตรวจสอบรอยโรคและแมลงศัตรูพืช โดยเฉพาะโรราเส้นดำ (Phytophthora)")

    # Section 3: News
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("📰 ข่าวสารอุตสาหกรรมและเทคโนโลยี")
    lines.append("")
    for item in news:
        lines.append(f"* **{item['title']}** ({item['source']})")
    lines.append("")

    # Section 4: Weather
    lines.append("---")
    lines.append("")
    end_date = now + timedelta(days=6)
    lines.append(f"🌦 แนวโน้มอากาศ 7 วัน ({now.strftime('%d')}–{end_date.strftime('%d')} {now.strftime('%B')} {now.strftime('%Y')})")
    lines.append("")
    lines.append("| พื้นที่ | อุณหภูมิ (°C) | โอกาสฝน (%) | ลม | ผลกระทบเกษตร |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    for w in weather:
        lines.append(f"| {w['name']} | {w['temp']} | {w['rain']} | {w['wind']} | {w['impact']} |")
    lines.append("")

    # Section 5: Fertilizer
    lines.append("---")
    lines.append("")
    lines.append("🌱 การจัดการปุ๋ยและสารกำจัดวัชพืช")
    lines.append("")
    lines.append(f"{fertilizer['source_note']}:")
    for key in ["urea", "npk", "mop", "organic"]:
        f = fertilizer[key]
        lines.append(f"- **{f['name']}**: ราคาขายส่งประมาณ {f['price_range']} {f['unit']}")
    lines.append("")
    lines.append("ในช่วงฤดูฝนควรใส่ปุ๋ยยูเรียแบบแบ่งครั้ง ทุก 45 วัน เพื่อลดการชะล้าง")
    lines.append("")
    lines.append("#### การจัดการวัชพืชและโรคพืช")
    lines.append("- **วัชพืช**: ใช้ Glyphosate อัตรา 2–3 ลิตร/เฮกตาร์")
    lines.append("- **โรคใบร่วงยาง**: ฉีดพ่น Metalaxyl ทุก 14 วันในช่วงฤดูฝน")
    lines.append("- **โรคทะลายเน่าปาล์ม**: ตัดแต่งทางใบที่แน่นทึบออก")

    # Footer
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"> เนื้อหานี้สร้างโดย GitHub Actions สำหรับเกษตรกร")
    lines.append(f"> ข้อมูลราคาล่าสุดเมื่อ {now.strftime('%H:%M')} น. เวลาไทย / 数据生成时间: {now.strftime('%Y-%m-%d %H:%M')} (UTC+7)")

    return "\n".join(lines)


# ============================================================
# LINE PUSH VIA CLOUDFLARE WORKER
# ============================================================

def push_to_line(text: str) -> bool:
    """Push report to LINE via Cloudflare Worker"""
    try:
        url = f"{WORKER_URL}/?message=" + requests.utils.quote(text[:4900])
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"LINE push via Worker: {data}")
            # Handle multi-chunk for long reports
            if len(text) > 4900:
                remaining = text[4900:]
                chunk = 2
                while remaining:
                    part = remaining[:4900]
                    remaining = remaining[4900:]
                    url2 = f"{WORKER_URL}/?message=" + requests.utils.quote(part)
                    requests.get(url2, timeout=30)
                    logger.info(f"Sent chunk {chunk}")
                    chunk += 1
            return True
        else:
            logger.error(f"Worker returned {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Worker push failed: {e}")
        return False


# ============================================================
# MAIN
# ============================================================

def main():
    logger.info("=" * 60)
    logger.info("📊 Farmer Daily Report (GitHub Actions)")
    logger.info(f"Worker URL: {WORKER_URL}")
    logger.info("=" * 60)

    report = generate_report()
    logger.info(f"Report generated: {len(report)} chars")

    # Print preview
    print("\n" + "=" * 40)
    print(report[:1000])
    print("...\n" + "=" * 40)

    # Push to LINE
    ok = push_to_line(report)
    if ok:
        logger.info("✅ Report pushed to LINE via Worker")
    else:
        logger.error("❌ LINE push failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
