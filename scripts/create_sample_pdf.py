"""生成一份不含个人信息的 OpsPilot 测试 PDF。"""

from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def create_sample_pdf(output_path: Path) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference}),
    })

    content = DecodedStreamObject()
    content.set_data(
        b"BT\n"
        b"/F1 16 Tf\n72 720 Td\n(OpsPilot Demo Policy) Tj\n"
        b"/F1 12 Tf\n0 -32 Td\n(Refunds are returned within three business days.) Tj\n"
        b"0 -24 Td\n(High priority tickets receive a first response within twenty minutes.) Tj\n"
        b"ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_metadata({"/Title": "OpsPilot Demo Policy"})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as output_file:
        writer.write(output_file)


def main() -> int:
    output_path = Path(__file__).resolve().parents[1] / "sample_documents" / "opspilot_demo_policy.pdf"
    create_sample_pdf(output_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
