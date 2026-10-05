import os, datetime as dt
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import simpleSplit
from reportlab.lib import colors

OUT = "/home/claude/kit/invoices"
FD = "/usr/share/fonts/truetype"
FONTS = {
    "DejaVu": (f"{FD}/dejavu/DejaVuSans.ttf", f"{FD}/dejavu/DejaVuSans-Bold.ttf"),
    "Carlito": (f"{FD}/crosextra/Carlito-Regular.ttf", f"{FD}/crosextra/Carlito-Bold.ttf"),
    "Poppins": (f"{FD}/google-fonts/Poppins-Regular.ttf", f"{FD}/google-fonts/Poppins-Bold.ttf"),
    "FreeSerif": (f"{FD}/freefont/FreeSerif.ttf", f"{FD}/freefont/FreeSerifBold.ttf"),
    "FreeMono": (f"{FD}/freefont/FreeMono.ttf", f"{FD}/freefont/FreeMonoBold.ttf"),
    "FreeSans": (f"{FD}/freefont/FreeSans.ttf", f"{FD}/freefont/FreeSansBold.ttf"),
}
for n, (r, b) in FONTS.items():
    pdfmetrics.registerFont(TTFont(n, r))
    pdfmetrics.registerFont(TTFont(n + "-B", b))

BUYER = ["Kestrel Components Pvt Ltd", "Plot No. 14, MIDC Bhosari",
         "Pune, Maharashtra 411026", "GSTIN: 27AAKCK4821M1Z3"]


def inr(x, sym=False):
    neg = x < 0
    x = abs(round(x, 2))
    ip = int(x)
    dec = f"{x - ip:.2f}"[1:]
    s = str(ip)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return ("-" if neg else "") + ("₹ " if sym else "") + s + dec


ONES = "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen".split()
TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _two(n):
    return ONES[n] if n < 20 else TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])


def _three(n):
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(ONES[h] + " Hundred")
    if r:
        parts.append(_two(r))
    return " ".join(parts)


def words(n):
    n = int(round(n))
    parts = []
    for div, name in [(10**7, "Crore"), (10**5, "Lakh"), (1000, "Thousand")]:
        q, n = divmod(n, div)
        if q:
            parts.append(_two(q) + " " + name if q < 100 else _three(q) + " " + name)
    if n:
        parts.append(_three(n))
    return "Rupees " + " ".join(parts) + " Only"


def draw(inv):
    path = os.path.join(OUT, inv["file"])
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle(f"Invoice {inv['invoice_no']}")
    W, H = A4
    M = 40
    F, FB = inv["font"], inv["font"] + "-B"
    acc = colors.HexColor(inv["accent"])
    plain = inv["style"] == "mono"
    v = inv["vendor"]
    vlines = v["addr"] + [f"GSTIN: {v['gstin']}", f"{v['phone']}  |  {v['email']}"]
    st = inv["style"]

    # ---------- header ----------
    if st == "band":
        c.setFillColor(acc); c.rect(0, H - 118, W, 118, fill=1, stroke=0)
        c.setFillColor(colors.white); c.setFont(FB, 19); c.drawString(M, H - 48, v["name"])
        c.setFont(F, 8.5); yy = H - 66
        for l in vlines:
            c.drawString(M, yy, l); yy -= 11
        c.setFont(FB, 15); c.drawRightString(W - M, H - 48, inv["title"])
        y = H - 140
    elif st == "center":
        c.setFillColor(acc); c.setFont(FB, 18); c.drawCentredString(W / 2, H - 50, v["name"].upper())
        c.setFillColor(colors.black); c.setFont(F, 8.5); yy = H - 66
        for l in vlines:
            c.drawCentredString(W / 2, yy, l); yy -= 11
        c.setStrokeColor(acc); c.setLineWidth(1.5); c.line(M, yy - 2, W - M, yy - 2)
        c.setFont(FB, 13); c.setFillColor(acc); c.drawCentredString(W / 2, yy - 20, inv["title"])
        y = yy - 40
    elif st == "mono":
        c.setFont(FB, 13); c.drawString(M, H - 48, v["name"])
        c.setFont(F, 8.5); yy = H - 62
        for l in vlines:
            c.drawString(M, yy, l); yy -= 10.5
        c.setFont(FB, 12); c.drawRightString(W - M, H - 48, inv["title"])
        c.setFont(F, 8.5); c.drawString(M, yy - 6, "-" * 92)
        y = yy - 24
    else:  # left
        c.setFillColor(acc); c.setFont(FB, 20); c.drawString(M, H - 50, v["name"])
        c.setFillColor(colors.HexColor("#333333")); c.setFont(F, 8.5); yy = H - 66
        for l in vlines:
            c.drawString(M, yy, l); yy -= 11
        c.setFillColor(acc); c.setFont(FB, 16); c.drawRightString(W - M, H - 50, inv["title"])
        c.setStrokeColor(acc); c.setLineWidth(2); c.line(M, yy - 4, W - M, yy - 4)
        y = yy - 24

    # ---------- bill-to + meta ----------
    c.setFillColor(colors.black)
    c.setFont(FB, 9); c.drawString(M, y, inv.get("billto_label", "Bill To"))
    c.setFont(F, 9); yy = y - 13
    for l in BUYER:
        c.drawString(M, yy, l); yy -= 12
    my = y
    vw = max(pdfmetrics.stringWidth(val, F, 9) for _, val in inv["meta"])
    valx = min(W - M - 122, W - M - vw)
    for label, val in inv["meta"]:
        c.setFont(FB, 9); c.drawRightString(valx - 8, my, label)
        c.setFont(F, 9); c.drawString(valx, my, val); my -= 13
    y = min(yy, my) - 14

    # ---------- line table ----------
    cols = [("#", 20), ("Description", 205), ("HSN/SAC", 52), ("Qty", 42), ("UOM", 38), (inv.get("rate_label", "Rate"), 70), ("Amount", 88)]
    xs = [M]
    for _, w in cols:
        xs.append(xs[-1] + w)
    hh = 18
    if plain:
        c.setFont(F, 8.5); c.drawString(M, y + 4, "-" * 92)
    else:
        c.setFillColor(acc); c.rect(M, y - hh + 12, W - 2 * M, hh, fill=1, stroke=0)
    c.setFillColor(colors.black if plain else colors.white); c.setFont(FB, 8.5)
    for i, (name, w) in enumerate(cols):
        if i >= 3:
            c.drawRightString(xs[i + 1] - 4, y - 1, name)
        else:
            c.drawString(xs[i] + 3, y - 1, name)
    y -= hh
    if plain:
        c.setFont(F, 8.5); c.drawString(M, y + 6, "-" * 92)
    c.setFillColor(colors.black)
    for idx, ln in enumerate(inv["lines"], 1):
        desc_lines = simpleSplit(ln["desc"], F, 8.5, 205 - 6)
        rowh = 12 * len(desc_lines) + 6
        c.setFont(F, 8.5)
        c.drawString(xs[0] + 3, y - 2, str(idx))
        for k, dl in enumerate(desc_lines):
            c.drawString(xs[1] + 3, y - 2 - 12 * k, dl)
        c.drawString(xs[2] + 3, y - 2, ln["hsn"])
        q = ln["qty"]
        c.drawRightString(xs[4] - 4, y - 2, f"{q:g}")
        c.drawRightString(xs[5] - 4, y - 2, ln["uom"])
        c.drawRightString(xs[6] - 4, y - 2, inr(ln["rate"]))
        c.drawRightString(xs[7] - 4, y - 2, inr(ln["qty"] * ln["rate"]))
        y -= rowh
        if not plain:
            c.setStrokeColor(colors.HexColor("#DDDDDD")); c.setLineWidth(0.5); c.line(M, y + 4, W - M, y + 4)
    if plain:
        c.setFont(F, 8.5); c.drawString(M, y + 2, "-" * 92)

    # ---------- totals ----------
    sub = sum(l["qty"] * l["rate"] for l in inv["lines"])
    rows = []
    mode = inv["tax_mode"]
    if mode == "split":
        cg = round(sub * 0.09, 2)
        rows = [("Sub Total", sub), ("CGST @ 9%", cg), ("SGST @ 9%", cg)]
        total = sub + 2 * cg
    elif mode == "igst":
        ig = round(sub * 0.18, 2)
        rows = [("Taxable Value", sub), ("IGST @ 18%", ig)]
        total = sub + ig
    else:  # inclusive - tax embedded, not broken out
        total = sub
        rows = []
    y -= 10
    lx, rx = W - M - 210, W - M - 4
    for label, amt in rows:
        c.setFont(F, 9); c.drawString(lx, y, label); c.drawRightString(rx, y, inr(amt)); y -= 14
    if not plain:
        c.setFillColor(colors.HexColor("#F1F1F1")); c.rect(lx - 6, y - 6, 216, 20, fill=1, stroke=0)
        c.setFillColor(colors.black)
    tl = inv.get("total_label", "Total Amount")
    c.setFont(FB, 10.5); c.drawString(lx, y, tl); c.drawRightString(rx, y, inr(total, sym=True))
    y -= 22
    if mode == "inclusive":
        c.setFont(F, 8); c.drawRightString(rx, y + 6, "All amounts are inclusive of IGST @ 18%")
        y -= 8
    c.setFont(FB, 8.5); c.drawString(M, y, "Amount in words:")
    lw = pdfmetrics.stringWidth("Amount in words:", FB, 8.5) + 6
    c.setFont(F, 8.5); c.drawString(M + lw, y, words(total))
    y -= 26

    # ---------- bank / notes ----------
    c.setFont(FB, 9); c.drawString(M, y, "Bank Details"); y -= 12
    c.setFont(F, 8.5)
    for l in inv["bank"]:
        c.drawString(M, y, l); y -= 11
    y -= 8
    if inv.get("notes"):
        c.setFont(FB, 9); c.drawString(M, y, "Notes"); y -= 12
        c.setFont(F, 8.5)
        for n in inv["notes"]:
            for dl in simpleSplit(n, F, 8.5, 300):
                c.drawString(M, y, dl); y -= 11

    if not plain:
        c.setFont(F, 9)
        c.drawRightString(W - M, 120, f"For {v['name']}")
        c.setStrokeColor(colors.black); c.setLineWidth(0.5); c.line(W - M - 150, 80, W - M, 80)
        c.drawRightString(W - M, 68, "Authorised Signatory")
    c.setFont(F, 7); c.setFillColor(colors.HexColor("#777777"))
    c.drawCentredString(W / 2, 30, inv.get("footer", "This is a computer generated invoice."))
    c.save()
    return path, sub, total


def due(d, days):
    return (dt.datetime.strptime(d, "%d-%b-%Y") + dt.timedelta(days=days)).strftime("%d-%b-%Y")


SAH = dict(name="Sahyadri Steel Traders", addr=["Gat No. 112, Chakan Industrial Area", "Pune, Maharashtra 410501"],
           gstin="27AAHFS5521K1ZQ", phone="+91 20 2745 1180", email="accounts@sahyadristeel.in")
MER = dict(name="Meridian Office Supplies", addr=["Shop 7, Lakshmi Road, Sadashiv Peth", "Pune, Maharashtra 411030"],
           gstin="27AAPFM4473D1Z6", phone="+91 98220 41736", email="sales@meridianoffice.in")
BRI = dict(name="Brightline Logistics LLP", addr=["Unit 304, Transport Nagar, Kalamboli", "Navi Mumbai, Maharashtra 410218"],
           gstin="27AAKFB8834P1Z2", phone="+91 22 2742 9900", email="billing@brightlinelogistics.in")
DEC = dict(name="Deccan Packaging Solutions", addr=["S. No. 41, Pirangut MIDC", "Pune, Maharashtra 412115"],
           gstin="27AAMFD2290L1Z8", phone="+91 20 6711 2045", email="accounts@deccanpack.in")
ORB = dict(name="Orbit IT Services Pvt Ltd", addr=["3rd Floor, Prestige Tech Park, Outer Ring Road", "Bengaluru, Karnataka 560103"],
           gstin="29AACCO7716R1ZK", phone="+91 80 4120 6633", email="finance@orbitits.co.in")

INVOICES = [
    dict(file="01_happy_sahyadri_steel.pdf", font="DejaVu", accent="#1F3A5F", style="left", vendor=SAH,
         title="TAX INVOICE", invoice_no="SST/26-27/0318",
         meta=[("Invoice No:", "SST/26-27/0318"), ("Invoice Date:", "15-Sep-2026"), ("PO No:", "PO-2026-0101"),
               ("Due Date:", due("15-Sep-2026", 45)), ("Place of Supply:", "Maharashtra (27)")],
         lines=[dict(desc="TMT bars Fe500D 12mm", hsn="7214", qty=20, uom="MT", rate=14500),
                dict(desc="MS angle 40x40x5mm", hsn="7216", qty=8, uom="MT", rate=13750)],
         tax_mode="split",
         bank=["Bank of Maharashtra, Chakan Branch", "A/c No: 60198423334417   IFSC: MAHB0001234"],
         notes=["Material dispatched vide e-way bill 3410 9921 7735 on 14-Sep-2026."]),

    dict(file="02_happy_tolerance_meridian.pdf", font="Poppins", accent="#0E7C66", style="band", vendor=MER,
         title="TAX INVOICE", invoice_no="MOS-2026-1187",
         meta=[("Invoice #", "MOS-2026-1187"), ("Date", "22-Sep-2026"), ("Your Order Ref", "PO-2026-0106"),
               ("Terms", "15 days"), ("Place of Supply", "Maharashtra")],
         lines=[dict(desc="A4 copier paper 75 GSM", hsn="4802", qty=200, uom="Ream", rate=285),
                dict(desc="Toner cartridge HP 26A compatible", hsn="8443", qty=12, uom="Nos", rate=3450),
                dict(desc="Box files with clip", hsn="4820", qty=300, uom="Nos", rate=42),
                dict(desc="Courier & handling charges", hsn="9968", qty=1, uom="Lot", rate=1600)],
         tax_mode="split",
         bank=["HDFC Bank, Tilak Road", "A/c No: 50200077113390   IFSC: HDFC0000456"],
         notes=["Thank you for your business!", "Goods once sold will not be taken back."]),
]

BRI_COMMON = dict(font="Carlito", accent="#B4441B", style="center", vendor=BRI, title="TAX INVOICE",
                  tax_mode="split", rate_label="Rate/Unit",
                  bank=["ICICI Bank, Kalamboli", "A/c No: 012305009032   IFSC: ICIC0000123"])
for i, (no, date, lines, note) in enumerate([
    ("BL/INV/2026/0811", "05-Sep-2026",
     [dict(desc="FTL freight Pune to Chennai, 32ft MXL container (trips 1-8, 26 Aug - 03 Sep)", hsn="9965", qty=8, uom="Trip", rate=25000)],
     "Part billing against Q3 contract. LR copies attached separately."),
    ("BL/INV/2026/0857", "18-Sep-2026",
     [dict(desc="FTL freight Pune to Chennai, 32ft MXL container (trips 9-16, 04 Sep - 16 Sep)", hsn="9965", qty=8, uom="Trip", rate=25000)],
     "Part billing against Q3 contract. LR copies attached separately."),
    ("BL/INV/2026/0902", "29-Sep-2026",
     [dict(desc="FTL freight Pune to Chennai, 32ft MXL container (trips 17-21, 17 Sep - 28 Sep)", hsn="9965", qty=5, uom="Trip", rate=25000),
      dict(desc="Detention charges at Chennai consignee (6 vehicle-days)", hsn="9967", qty=6, uom="Day", rate=2500)],
     "Final billing for Q3. Extra trip on 26-Sep requested by your dispatch team over phone."),
], 1):
    INVOICES.append(dict(BRI_COMMON, file=f"0{2 + i}_split_brightline_part{i}.pdf", invoice_no=no,
                         meta=[("Invoice No.", no), ("Invoice Date", date), ("PO#", "2026-0102"),
                               ("Payment Terms", "30 days"), ("Place of Supply", "Maharashtra (27)")],
                         lines=lines, notes=[note]))

DEC_LINES = [dict(desc="Corrugated boxes 5-ply 600x400x400mm", hsn="4819", qty=2000, uom="Nos", rate=38),
             dict(desc="Stretch wrap film 500mm x 23 micron", hsn="3920", qty=50, uom="Roll", rate=620)]
INVOICES += [
    dict(file="06_dup_deccan_original.pdf", font="FreeSerif", accent="#5B2A86", style="left", vendor=DEC,
         title="TAX INVOICE", invoice_no="INV-0042",
         meta=[("Invoice No:", "INV-0042"), ("Date:", "12-Sep-2026"), ("PO Ref:", "PO-2026-0103"),
               ("Due Date:", due("12-Sep-2026", 30)), ("Place of Supply:", "Maharashtra (27)")],
         lines=DEC_LINES, tax_mode="split",
         bank=["Saraswat Co-op Bank, Paud Road", "A/c No: 110200101012268   IFSC: SRCB0000089"],
         notes=["Delivered to Bhosari warehouse, GRN pending."]),
    dict(file="07_dup_deccan_resubmit.pdf", font="FreeMono", accent="#000000", style="mono", vendor=DEC,
         title="INVOICE", invoice_no="42", billto_label="BUYER", rate_label="Unit Price",
         meta=[("Invoice No.", "42"), ("Inv. Date", "19/09/2026"), ("Ref PO", "PO/2026/0103"),
               ("Terms", "Net 30")],
         lines=DEC_LINES, tax_mode="split", total_label="GRAND TOTAL",
         bank=["Saraswat Co-op Bank, Paud Road", "A/c No: 110200101012268   IFSC: SRCB0000089"],
         notes=["Payment reminder - kindly process at the earliest.",
                "Generated from our new billing system (TallyPrime migration)."],
         footer="System generated document. No signature required."),
    dict(file="08_scanned_orbit_no_po.pdf", font="FreeSans", accent="#22313F", style="left", vendor=ORB,
         title="TAX INVOICE", invoice_no="OITS/KA/26-27/2291", rate_label="Rate (incl. tax)",
         meta=[("Invoice No:", "OITS/KA/26-27/2291"), ("Date:", "24 Sep 2026"),
               ("Your Ref:", "Email dt. 04-Sep-2026 (Mr. Sandeep)"), ("Place of Supply:", "Maharashtra (27)")],
         lines=[dict(desc="AMC - laptop fleet, 45 nos (Oct 2026 - Sep 2027)", hsn="9987", qty=45, uom="Nos", rate=3304),
                dict(desc="Network security audit & VAPT report", hsn="9983", qty=1, uom="Job", rate=40120)],
         tax_mode="inclusive", total_label="Invoice Total",
         bank=["Axis Bank, Bellandur", "A/c No: 921020047557751   IFSC: UTIB0002841"],
         notes=["Inter-state supply. IGST applicable.", "Please quote invoice number with remittance."]),
]

SAH_SPOOF = dict(SAH, email="accounts@sahyadri-steel.in", phone="+91 20 2745 1180")
INVOICES.append(
    dict(file="09_bank_change_sahyadri.pdf", font="DejaVu", accent="#1F3A5F", style="left", vendor=SAH_SPOOF,
         title="TAX INVOICE", invoice_no="SST/26-27/0341",
         meta=[("Invoice No:", "SST/26-27/0341"), ("Invoice Date:", "28-Sep-2026"), ("PO No:", "PO-2026-0108"),
               ("Due Date:", due("28-Sep-2026", 45)), ("Place of Supply:", "Maharashtra (27)")],
         lines=[dict(desc="MS plate 10mm IS2062 E250", hsn="7208", qty=10, uom="MT", rate=25000)],
         tax_mode="split",
         bank=["Kotak Mahindra Bank, Andheri East Branch", "A/c No: 7412093658820   IFSC: KKBK0000631"],
         notes=["IMPORTANT: Our bank details have changed with effect from 01-Sep-2026.",
                "Please update your vendor records and remit all payments to the account above.",
                "Material dispatched vide e-way bill 3410 9921 8102 on 27-Sep-2026."]))

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for inv in INVOICES:
        p, sub, tot = draw(inv)
        print(f"{inv['file']:38s} subtotal={inr(sub):>12s} total={inr(tot):>12s}")
