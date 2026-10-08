# RyoNex OBF Lab 0.2.0

สถานะ: engine Lua 5.1 แบบ local ที่รันได้ + ชุดค้นคว้า 27 แหล่ง + deobfuscator สำหรับวิจัย ไม่ใช่ obfuscator ใหม่ที่พิสูจน์แล้วว่าเหนือกว่า Luraph และไม่ได้รวมทุกโครงการบนอินเทอร์เน็ต

Based on Prometheus by Elias Oelschner, https://github.com/prometheus-lua/Prometheus

แพ็กนี้เป็น wrapper/งานต่อยอดบน Prometheus ไม่ใช่ VM ที่ RyoNex เขียนใหม่ เก็บ license และเครดิตต้นฉบับไว้แล้ว รวม unprom แบบ MIT แยกไว้ใน research-tools สำหรับประเมินผล ไม่เอา deobfuscator มารันเป็นชั้น obfuscation

## ใช้บน Windows PowerShell

ติดตั้ง Python 3.11+ แล้วเปิด terminal ในโฟลเดอร์นี้:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe engine.py doctor
.\.venv\Scripts\python.exe engine.py protect input.lua output.lua --profile hardened
```

Linux ใช้ `python3 -m venv .venv` และ `.venv/bin/python` แทน Python ของ Windows
ต้องมีอินเทอร์เน็ตตอนติดตั้ง dependency หลังจากนั้น build ทำงาน local ไม่ส่งซอร์สไปบริการภายนอก

## โปรไฟล์

| โปรไฟล์ | การแปลง | ข้อแลกเปลี่ยน |
|---|---|---|
| balanced | EncryptStrings → Vmify → ConstantArray → NumbersToExpressions → WrapInFunction | VM หนึ่งชั้น |
| hardened | Vmify → EncryptStrings → Vmify → ConstantArray → NumbersToExpressions → WrapInFunction | VM สองชั้น อาจช้าและไฟล์ใหญ่ขึ้น ไม่ได้แปลว่าแกะไม่ได้ |

ทั้งสองโปรไฟล์ตัด AntiTamper ของ upstream ออกจาก pipeline เพราะสมมติฐานเกี่ยวกับ runtime อาจทำให้โค้ดพัง ใช้ seed สุ่มต่อ build; `--seed 42` ใช้ทำซ้ำเพื่อ debug ไม่ใช่ secret หรือกุญแจเข้ารหัส

ไฟล์เข้า/ออกจำกัด 8 MiB, timeout เริ่มต้น 120 วินาที ปรับได้สูงสุด 720 วินาที, จำกัด allocator ของ Lua 512 MiB ซึ่งไม่ใช่ขีดจำกัดหน่วยความจำทั้ง process มี worker แยกและไม่ execute source ของลูกค้าขณะ build ตรวจ output ด้วย Lua 5.1 compiler โดยไม่รันมัน ตรวจ hash ของ engine ก่อน build และเผยแพร่ผลแบบ atomic hard link โดยไม่เขียนทับไฟล์เดิม (ต้องใช้ filesystem ที่รองรับ hard link)

## ผลทดสอบที่ทำจริง — 0.2.0

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests/stress.py
.\.venv\Scripts\python.exe tests/benchmark.py
```

- 16 unit test methods ผ่าน รวม worker errors, timeout (จำลอง), collision, symlink, UTF-8, input bounds, ไม่ execute source ระหว่าง build และ compatibility guards
- 80 fixtures × 2 profiles × 3 seeds × 2 runtimes = 960 differential comparisons ผ่านบน Lua 5.1 และ LuaJIT 2.1
- fixtures 40 ชุดเป็น generated arithmetic/loop cases; อีก 40 ชุดเป็น authored edge cases ไม่ใช่ 960 โปรแกรมอิสระ
- เปรียบเทียบจำนวนและค่าที่คืน รวม nil ท้ายรายการ ใช้ instruction budget ใน test runner
- ข้อผิดพลาดที่พบและแก้: parentheses ที่ควรคืนค่าเดียว, nil ท้าย return/varargs; ตรวจพบ setfenv semantics ที่ยังไม่รองรับจึงปฏิเสธ direct identifiers setfenv/getfenv/debug ก่อน build
- Guard นี้ conservative และไม่ใช่ sandbox; dynamic/aliased introspection ก็ยังไม่รองรับ แม้ตรวจจับไม่ได้
- unprom ทั้ง 6 probes สร้างไฟล์ได้ แต่ผลที่แกะไม่ผ่านการรันเทียบต้นฉบับ ไม่ใช่หลักฐานว่าไม่มีใครแกะได้
- หลักฐาน: evidence/tests.txt, evidence/stress.json, evidence/benchmark.json, evidence/deob_probe_v2.json
- หลักฐานรุ่นเก่าแยกไว้ใน evidence/baseline_v0.1

## ความเร็วที่วัดได้

งานตัวอย่าง loop 5,000 รอบใน Lua 5.1 บนเครื่องทดสอบนี้ ใช้ค่ามัธยฐาน 9 รอบ:

| แบบ | เวลารัน | เทียบต้นฉบับ | ขนาด |
|---|---:|---:|---:|
| ต้นฉบับ | 0.044 ms | 1× | 49 bytes |
| balanced | 0.869 ms | 19.9× | 11,827 bytes |
| hardened | 6.328 ms | 144.8× | 26,168 bytes |

ตัวเลขเป็น microbenchmark เดียว ไม่ทำนายประสิทธิภาพ Roblox หรือสคริปต์จริง จึงคง balanced เป็นค่าเริ่มต้น hardened เป็นตัวเลือกที่ต้องวัดกับงานจริง ไม่มีคะแนนความปลอดภัยจากจำนวนชั้น VM หรือขนาดไฟล์

## ข้อจำกัดปัจจุบัน

รองรับเป้าหมาย Lua 5.1 เท่านั้น ไม่ได้ยืนยัน Luau types, continue, generalized iteration, interpolation หรือ Roblox APIs การเอา Luau เต็มรูปแบบมาใช้สามารถถูกปฏิเสธได้ อย่าตัด syntax ด้วย regex เพื่อให้ผ่าน

นี่เป็น local workbench ไม่ใช่บริการรับโค้ดหลายผู้ใช้ที่ผ่าน security review แล้ว ก่อนเปิดเป็น SaaS ต้องเพิ่ม worker/container isolation, quota/concurrency, memory/process limits และเชื่อม policy ของเว็บ ไม่เปิด HTTP endpoint รับคำสั่ง shell

ยังไม่ได้แก้หรือเชื่อมระบบเว็บ RyoNex, ไม่ได้ทดสอบ DirectAdmin/Roblox, ไม่ได้ทำ independent VM และไม่ได้ทดสอบเทียบ Luraph รุ่นปัจจุบัน ลิงก์เอกสารทางการที่ค้นพบระบุ v15 แต่ไม่ยืนยันเลข patch ล่าสุด

## การใช้เชิงพาณิชย์

อ่าน `vendor/prometheus/LICENSE` ฉบับที่แนบ โดยเฉพาะเครดิตสำหรับผลิตภัณฑ์และ public-facing SaaS UI ห้ามลบเครดิตแล้วอ้างเป็นเครื่องมือเขียนใหม่ทั้งหมด การเพิ่มโครงการอื่นต้องอ่าน license ของ commit นั้นก่อน รายการลิงก์ไม่ได้แปลว่าได้รับสิทธิ์รวมซอร์ส

## เป้าหมายถัดไปที่ต้องมีหลักฐาน

1. เพิ่ม Luau frontend/IR และ differential tests บน Luau runtime จริง ก่อนประกาศรองรับ Roblox
2. ถ้าต้องการ VM ของ RyoNex ให้พัฒนา compiler/runtime ของตัวเอง พร้อม semantics ของ closure, varargs, upvalues, coroutine และ metamethod
3. ใช้ deobfuscators เป็นชุดประเมินแยก คัดเฉพาะ license และเวอร์ชันที่ตรวจสอบแล้ว
4. เปรียบเทียบกับ output Luraph ที่สร้างจาก source ทดสอบชุดเดียวกัน บันทึกเวอร์ชัน, preset, build time, runtime overhead, ขนาด และคุณภาพการกู้ logic ด้วยงบเวลาวิเคราะห์เท่ากัน
5. รายงานว่าดีกว่าเฉพาะด้านที่วัดได้ ไม่มีเกณฑ์ไฟล์ใหญ่กว่า/VM มากชั้นกว่าจึงปลอดภัยกว่า
