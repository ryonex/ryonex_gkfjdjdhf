# จุดเชื่อมกับ RyoNex ที่ตรวจแล้ว

ตรวจ source จาก RYONEX_CLINE_OVERNIGHT_PACK (12).zip ที่ผู้ใช้มีอยู่ โดยไม่ได้แก้แพ็กต้นฉบับ

- `src/Builds/ProtectionEngine.php`: contract ของ engine มี id/version/available/protect และกำหนด fixed argv, resource bounds, stable errors
- `src/Builds/DeterministicTestEngine.php`: ระบุชัดว่าเป็น orchestration test envelope ไม่ใช่การป้องกันโค้ด
- `src/Builds/UnavailableEngine.php`: พบ implementation ของสถานะไม่พร้อมใช้งาน

แพ็ก OBF Lab มี engine CLI จริง แต่ยังไม่มี production PHP adapter และยังไม่ถูก register เข้ากับเว็บ การนำโฟลเดอร์นี้ไปวางในเว็บเฉย ๆ ไม่ทำให้ build pipeline เชื่อมต่อโดยอัตโนมัติ

แนวทางเชื่อม: trusted worker เรียก Python executable และ engine.py ที่กำหนดไว้ตายตัว ส่งไฟล์จาก sealed source ผ่าน private temporary directory แล้วตรวจ exit code/size/checksum ก่อนสร้าง ProtectionResult ห้ามส่ง source ผ่าน command string หรือ log; ห้ามนำ engine นี้ไปวางใน public document root; ไม่ใช้ deterministic test adapter เป็น fallback เมื่อ build จริงล้มเหลว

เปิดใช้งานจริงได้หลังตรวจ credential separation, process/container isolation, concurrent jobs, timeout/lease cancellation และทดสอบ tenant boundary ครบ พร้อมเครดิต Prometheus ใน public-facing UI ตาม license
