# Remote-Fix Relay

```
remote-fix/
├── README.md
├── backend/          <- VPS ga
│   ├── main.py
│   ├── requirements.txt
│   ├── .env.example
│   └── run.sh
└── agent/            <- tuzatiladigan PC ga
    ├── agent.py
    ├── .env.example
    └── run.sh
```

## 1. VPS (backend)
```bash
cd backend && ./run.sh
```
Birinchi ishga tushishda `.env` o'zi yaratiladi va `ADMIN_KEY` / `AGENT_TOKEN` ekranga chiqadi.
Backend faqat `127.0.0.1:8765` da ishlaydi. TLS uchun Caddy (avtomatik Let's Encrypt):
```
vps.sizningdomen.uz {
    reverse_proxy 127.0.0.1:8765
}
```
Tekshirish: `curl https://vps.sizningdomen.uz/health`

## 2. PC (agent)
`agent/` papkasini PC ga ko'chiring (faqat Python 3, pip shart emas).
```bash
cd agent && ./run.sh      # birinchi marta .env yaratadi, to'ldirib qayta ishga tushiring
```
`.env`: `RELAY_URL=https://vps.sizningdomen.uz` va backend dagi `AGENT_TOKEN`.
Agent ochiq turgan terminalda qoladi. To'xtatish: **Ctrl+C**.

## 3. Sessiya berish (har safar)
1. Sandbox "Allowed domains" ga `vps.sizningdomen.uz` qo'shilgan bo'lishi kerak.
2. Qisqa muddatli token yarating (default 30 daqiqa, maks 120):
```bash
curl -s -X POST "https://vps.sizningdomen.uz/admin/session?ttl_minutes=30" -H "X-Admin-Key: <ADMIN_KEY>"
```
3. Chiqqan `token` ni chatga tashlang (ADMIN_KEY va AGENT_TOKEN ni hech qachon bermang).

## 4. Kill switch
- PC da: agentda Ctrl+C yoki `q` javobi.
- VPS da: `curl -s -X POST https://vps.sizningdomen.uz/admin/revoke -H "X-Admin-Key: <ADMIN_KEY>"`
  (barcha operator tokenlar va kutayotgan buyruqlar bekor qilinadi).

## Xavfsizlik
- Barcha buyruq PC terminalida ko'rinadi va `y/n` so'raydi.
- `AUTO_READONLY=1` da faqat ro'yxatdagi o'qish buyruqlari (uname, df, ls, ip a, systemctl status, journalctl...) shell belgilarisiz so'ramasdan bajariladi. `AUTO_READONLY=0` qilsangiz hammasini so'raydi.
- `sudo` parolini agent terminalida o'zingiz kiritasiz. Parol relay orqali o'tmaydi.
- Loglar: `backend/relay.log`, `agent/agent.log`. 10 ta xato urinishdan keyin IP 5 daqiqaga bloklanadi.
