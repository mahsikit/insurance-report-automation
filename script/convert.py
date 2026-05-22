import os
import pandas as pd

def convert_excel_to_csv(INPUT_FOLDER, CONVERT_FOLDER):
    os.makedirs(CONVERT_FOLDER, exist_ok=True)

    print("🚀 START CONVERT EXCEL → CSV\n")

    files = os.listdir(INPUT_FOLDER)

    if not files:
        print("⚠️ Folder input kosong!")
        return

    excel_files = [f for f in files if f.endswith(".xlsx")]

    if not excel_files:
        print("⚠️ Tidak ada file Excel!")
        return

    print(f"📦 Ditemukan {len(excel_files)} file Excel\n")

    for file_name in excel_files:
        file_path = os.path.join(INPUT_FOLDER, file_name)
        print(f"📂 Processing: {file_name}")

        try:
            excel_file = pd.ExcelFile(file_path)

            for sheet in excel_file.sheet_names:
                df = pd.read_excel(file_path, sheet_name=sheet)

                safe_sheet = sheet.replace(" ", "_")
                output_name = f"{file_name.replace('.xlsx','')}_{safe_sheet}.csv"
                output_path = os.path.join(CONVERT_FOLDER, output_name)

                df.to_csv(output_path, index=False, encoding="utf-8-sig")

                print(f"   ✅ {output_name}")

        except Exception as e:
            print(f"   ❌ Error: {e}")

    print("\n🎯 CONVERT SELESAI\n")