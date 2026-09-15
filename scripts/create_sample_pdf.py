"""生成一页可检索的中文虚构售后制度 PDF（需要 reportlab 和中文字体）。"""

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def find_chinese_font() -> Path:
    configured = os.getenv("OPSPILOT_DEMO_FONT")
    candidates = [
        Path(configured) if configured else None,
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    ]
    for path in candidates:
        if path is not None and path.is_file():
            return path
    raise FileNotFoundError("请设置 OPSPILOT_DEMO_FONT 指向可用的中文 TrueType 字体")


def create_sample_pdf(output_path: Path) -> None:
    pdfmetrics.registerFont(TTFont("OpsPilotChinese", str(find_chinese_font())))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page = canvas.Canvas(str(output_path), pagesize=A4, pageCompression=1)
    page.setTitle("OpsPilot 虚构售后制度")

    width, height = A4
    green = colors.HexColor("#176548")
    muted = colors.HexColor("#52635B")
    page.setFillColor(green)
    page.rect(0, height - 14, width, 14, fill=1, stroke=0)
    page.setFont("OpsPilotChinese", 22)
    page.drawString(56, height - 88, "OpsPilot 虚构售后制度")

    page.setFillColor(muted)
    page.setFont("OpsPilotChinese", 10)
    page.drawString(56, height - 116, "仅用于教学演示；以下时效和业务数据均为虚构示例。")

    page.setStrokeColor(colors.HexColor("#D8E5DD"))
    page.line(56, height - 140, width - 56, height - 140)

    page.setFillColor(green)
    page.setFont("OpsPilotChinese", 14)
    page.drawString(56, height - 186, "退款到账时间")
    page.setFillColor(colors.HexColor("#24342B"))
    page.setFont("OpsPilotChinese", 11)
    page.drawString(56, height - 216, "普通退款：申请审核通过后，预计 3 个工作日内到账。")
    page.drawString(56, height - 242, "紧急退款：标记为紧急且审核通过后，预计 3 小时内到账。")

    page.setFillColor(green)
    page.setFont("OpsPilotChinese", 14)
    page.drawString(56, height - 302, "高优先级工单")
    page.setFillColor(colors.HexColor("#24342B"))
    page.setFont("OpsPilotChinese", 11)
    page.drawString(56, height - 332, "高优先级工单将在 20 分钟内获得首次响应。")

    page.setStrokeColor(colors.HexColor("#D8E5DD"))
    page.line(56, 72, width - 56, 72)
    page.setFillColor(muted)
    page.setFont("OpsPilotChinese", 9)
    page.drawString(56, 54, "OpsPilot Demo Policy  |  虚构资料  |  第 1 页")
    page.save()


def main() -> int:
    output_path = Path(__file__).resolve().parents[1] / "sample_documents" / "opspilot_demo_policy.pdf"
    create_sample_pdf(output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
