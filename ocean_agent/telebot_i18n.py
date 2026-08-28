# -*- coding: utf-8 -*-
"""Central-bot languages: flag keyboard first, then the member flow.

Order fixed by the user (2026-08-18): EN, ZH, JA, KO, then crypto-adoption
rank. Every user-facing free-tier string lives here as a static table -
no LLM at runtime, translations are baked in. tr() falls back to English
for any hole so a missing key can never crash the bot.

Three tables:
  T          message and answer templates, {} placeholders are filled by
             the caller with pre-formatted numbers (locale-neutral).
  INTENT     per-language keyword lists for the rule-based intent match.
             Matching is substring-in-lowercased-text, so keywords are
             lowercase; for inflected languages (ru, pt, es, tr) stems
             are used where that catches more forms.
  ALIAS_I18N per-language coin/stock names mapped to Pacifica symbols.
             Only unambiguous names are listed. The Korean ALIAS stays
             in telebot.py (legacy, always applied).
"""
import json

LANGS = [
    ("en", "🇺🇸 EN"), ("zh", "🇨🇳 中"), ("ja", "🇯🇵 日"), ("ko", "🇰🇷 한"),
    ("vi", "🇻🇳 VI"), ("hi", "🇮🇳 HI"), ("id", "🇮🇩 ID"), ("ru", "🇷🇺 RU"),
    ("pt", "🇧🇷 PT"), ("tr", "🇹🇷 TR"), ("es", "🇪🇸 ES"),
]

T = {
    "pick_lang": {
        "any": "🌊 Ocean Agent\nPlease choose your language"},
    "ask_addr": {
        "ko": ("환영합니다! 파시피카 지갑의 공개 주소(솔라나)를 붙여넣어 "
               "주세요.\n조회 전용이라 이 주소로는 거래·출금이 불가능하며, "
               "봇 운영자는 해당 주소의 활동을 볼 수 있습니다."),
        "en": ("Welcome! Please paste the PUBLIC address (Solana) of your "
               "Pacifica wallet.\nView-only: it can never trade or "
               "withdraw. The operator can see that address's activity."),
        "zh": ("欢迎！请粘贴您 Pacifica 钱包的公开地址（Solana）。\n"
               "仅供查询：此地址无法进行交易或提现。"
               "机器人运营者可以看到该地址的活动。"),
        "ja": ("ようこそ！Pacifica ウォレットの公開アドレス（Solana）を"
               "貼り付けてください。\n照会専用のため、このアドレスでは"
               "取引・出金はできません。ボット運営者はこのアドレスの"
               "活動を確認できます。"),
        "vi": ("Chào mừng! Vui lòng dán địa chỉ CÔNG KHAI (Solana) của ví "
               "Pacifica của bạn.\nChỉ để xem: địa chỉ này không thể giao "
               "dịch hay rút tiền. Người vận hành bot có thể thấy hoạt động "
               "của địa chỉ này."),
        "hi": ("स्वागत है! कृपया अपने Pacifica वॉलेट का सार्वजनिक पता (Solana) "
               "पेस्ट करें।\nकेवल देखने के लिए: इस पते से कभी ट्रेड या निकासी "
               "नहीं हो सकती। बॉट संचालक इस पते की गतिविधि देख सकता है।"),
        "id": ("Selamat datang! Silakan tempel alamat PUBLIK (Solana) dompet "
               "Pacifica Anda.\nHanya untuk melihat: alamat ini tidak bisa "
               "dipakai untuk trading atau penarikan. Operator bot dapat "
               "melihat aktivitas alamat tersebut."),
        "ru": ("Добро пожаловать! Вставьте ПУБЛИЧНЫЙ адрес (Solana) вашего "
               "кошелька Pacifica.\nТолько для просмотра: с этого адреса "
               "нельзя торговать или выводить средства. Оператор бота может "
               "видеть активность этого адреса."),
        "pt": ("Bem-vindo! Cole o endereço PÚBLICO (Solana) da sua carteira "
               "Pacifica.\nSomente leitura: este endereço nunca pode "
               "negociar nem sacar. O operador do bot pode ver a atividade "
               "desse endereço."),
        "tr": ("Hoş geldiniz! Lütfen Pacifica cüzdanınızın HERKESE AÇIK "
               "adresini (Solana) yapıştırın.\nYalnızca görüntüleme "
               "içindir: bu adresle işlem yapılamaz veya para çekilemez. "
               "Bot operatörü bu adresin etkinliğini görebilir."),
        "es": ("¡Bienvenido! Pegue la dirección PÚBLICA (Solana) de su "
               "billetera Pacifica.\nSolo lectura: con esta dirección nunca "
               "se puede operar ni retirar. El operador del bot puede ver "
               "la actividad de esa dirección.")},
    "bad_addr": {
        "ko": "솔라나 주소 형식이 아닌 것 같습니다 (32~44자). 다시 확인해 주세요.",
        "en": "That does not look like a Solana address (32-44 chars). "
              "Please paste it again.",
        "zh": "这看起来不像 Solana 地址（32~44 个字符）。请重新粘贴。",
        "ja": "Solana アドレスの形式ではないようです（32〜44文字）。"
              "もう一度貼り付けてください。",
        "vi": "Có vẻ đây không phải địa chỉ Solana (32-44 ký tự). "
              "Vui lòng dán lại.",
        "hi": "यह Solana पते जैसा नहीं लगता (32-44 अक्षर)। कृपया फिर से पेस्ट करें।",
        "id": "Sepertinya itu bukan alamat Solana (32-44 karakter). "
              "Silakan tempel lagi.",
        "ru": "Это не похоже на адрес Solana (32-44 символа). "
              "Вставьте его ещё раз.",
        "pt": "Isso não parece um endereço Solana (32-44 caracteres). "
              "Cole novamente.",
        "tr": "Bu bir Solana adresine benzemiyor (32-44 karakter). "
              "Lütfen tekrar yapıştırın.",
        "es": "Eso no parece una dirección de Solana (32-44 caracteres). "
              "Péguela de nuevo."},
    "done": {
        "ko": ("등록 완료! 이제 자유롭게 물어보세요.\n"
               "예: 잔고 알려줘 · 비트코인 어때 · 픽 보여줘 · /pick"),
        "en": ("Registered! Ask me anything.\n"
               "Try: balance · how is BTC · show picks · /pick"),
        "zh": ("注册完成！随时提问吧。\n"
               "例如：查余额 · 比特币怎么样 · 看推荐 · /pick"),
        "ja": ("登録完了です！自由に質問してください。\n"
               "例：残高を教えて · ビットコインはどう · おすすめを見せて · /pick"),
        "vi": ("Đăng ký xong! Hãy hỏi bất cứ điều gì.\n"
               "Ví dụ: số dư · BTC thế nào · xem gợi ý · /pick"),
        "hi": ("पंजीकरण पूरा! कुछ भी पूछें।\n"
               "उदाहरण: बैलेंस बताओ · बिटकॉइन कैसा है · पिक दिखाओ · /pick"),
        "id": ("Pendaftaran selesai! Silakan tanya apa saja.\n"
               "Contoh: saldo · bagaimana BTC · lihat rekomendasi · /pick"),
        "ru": ("Регистрация завершена! Спрашивайте что угодно.\n"
               "Например: баланс · как биткоин · покажи рекомендации · /pick"),
        "pt": ("Cadastro concluído! Pergunte o que quiser.\n"
               "Ex.: saldo · como está o BTC · mostrar recomendações · /pick"),
        "tr": ("Kayıt tamamlandı! İstediğinizi sorabilirsiniz.\n"
               "Örnek: bakiye · BTC nasıl · seçimleri göster · /pick"),
        "es": ("¡Registro completado! Pregunte lo que quiera.\n"
               "Ej.: saldo · cómo está BTC · mostrar recomendaciones · /pick")},
    "menu": {
        "ko": ("오션 에이전트 (크레딧 불필요 모드)\n"
               "/pick - 추천픽 순위 (최근 계산본)\n"
               "/funding - 펀딩 순위 (파시피카)\n"
               "/carry - 펀딩캐리 자리·알람\n"
               "/bot - 봇 상태·최근 체결\n"
               "/balance - 계좌 잔고\n"
               "/trades - 최근 체결 이력\n"
               "명령은 고정식입니다. 주문은 이 봇으로 나가지 않습니다."),
        "en": ("Ocean Agent (no-credit mode)\n"
               "/pick - ranked picks (latest run)\n"
               "/funding - funding ranking (Pacifica)\n"
               "/carry - funding-carry spots and alerts\n"
               "/bot - bot status and recent fills\n"
               "/balance - account balance\n"
               "/trades - recent trade history\n"
               "Commands are fixed. No orders ever leave this bot."),
        "zh": ("Ocean Agent（免额度模式）\n"
               "/pick - 推荐排行（最新计算）\n"
               "/funding - 资金费率排行（Pacifica）\n"
               "/carry - 资金费套利机会与提醒\n"
               "/bot - 机器人状态与最近成交\n"
               "/balance - 账户余额\n"
               "/trades - 最近成交记录\n"
               "命令为固定格式。本机器人不会发出任何订单。"),
        "ja": ("Ocean Agent（クレジット不要モード）\n"
               "/pick - おすすめランキング（最新計算）\n"
               "/funding - ファンディング順位（Pacifica）\n"
               "/carry - ファンディングキャリーの候補・アラート\n"
               "/bot - ボットの状態・直近の約定\n"
               "/balance - 口座残高\n"
               "/trades - 直近の約定履歴\n"
               "コマンドは固定式です。このボットから注文が出ることは"
               "ありません。"),
        "vi": ("Ocean Agent (chế độ không cần credit)\n"
               "/pick - xếp hạng lựa chọn đề xuất (bản tính mới nhất)\n"
               "/funding - xếp hạng funding (Pacifica)\n"
               "/carry - cơ hội funding carry và cảnh báo\n"
               "/bot - trạng thái bot và các lệnh khớp gần đây\n"
               "/balance - số dư tài khoản\n"
               "/trades - lịch sử khớp lệnh gần đây\n"
               "Lệnh ở dạng cố định. Bot này không bao giờ đặt lệnh "
               "giao dịch."),
        "hi": ("Ocean Agent (बिना क्रेडिट मोड)\n"
               "/pick - अनुशंसित पिक रैंकिंग (नवीनतम गणना)\n"
               "/funding - फंडिंग रैंकिंग (Pacifica)\n"
               "/carry - फंडिंग कैरी अवसर और अलर्ट\n"
               "/bot - बॉट की स्थिति और हाल की ट्रेड\n"
               "/balance - खाते का बैलेंस\n"
               "/trades - हाल का ट्रेड इतिहास\n"
               "कमांड निश्चित प्रारूप में हैं। यह बॉट कभी कोई ऑर्डर नहीं भेजता।"),
        "id": ("Ocean Agent (mode tanpa kredit)\n"
               "/pick - peringkat pilihan rekomendasi (perhitungan terbaru)\n"
               "/funding - peringkat funding (Pacifica)\n"
               "/carry - peluang funding carry dan peringatan\n"
               "/bot - status bot dan transaksi terakhir\n"
               "/balance - saldo akun\n"
               "/trades - riwayat transaksi terakhir\n"
               "Perintah bersifat tetap. Bot ini tidak pernah mengirim "
               "order."),
        "ru": ("Ocean Agent (режим без кредитов)\n"
               "/pick - рейтинг рекомендаций (последний расчёт)\n"
               "/funding - рейтинг фандинга (Pacifica)\n"
               "/carry - возможности фандинг-керри и оповещения\n"
               "/bot - состояние бота и последние сделки\n"
               "/balance - баланс счёта\n"
               "/trades - история последних сделок\n"
               "Команды фиксированные. Этот бот никогда не отправляет "
               "ордера."),
        "pt": ("Ocean Agent (modo sem créditos)\n"
               "/pick - ranking de recomendações (cálculo mais recente)\n"
               "/funding - ranking de funding (Pacifica)\n"
               "/carry - oportunidades de funding carry e alertas\n"
               "/bot - status do bot e execuções recentes\n"
               "/balance - saldo da conta\n"
               "/trades - histórico de operações recentes\n"
               "Os comandos são fixos. Este bot nunca envia ordens."),
        "tr": ("Ocean Agent (kredisiz mod)\n"
               "/pick - önerilen seçim sıralaması (en son hesaplama)\n"
               "/funding - fonlama sıralaması (Pacifica)\n"
               "/carry - fonlama carry fırsatları ve uyarılar\n"
               "/bot - bot durumu ve son işlemler\n"
               "/balance - hesap bakiyesi\n"
               "/trades - son işlem geçmişi\n"
               "Komutlar sabittir. Bu bot asla emir göndermez."),
        "es": ("Ocean Agent (modo sin créditos)\n"
               "/pick - ranking de recomendaciones (último cálculo)\n"
               "/funding - ranking de funding (Pacifica)\n"
               "/carry - oportunidades de funding carry y alertas\n"
               "/bot - estado del bot y operaciones recientes\n"
               "/balance - saldo de la cuenta\n"
               "/trades - historial de operaciones recientes\n"
               "Los comandos son fijos. Este bot nunca envía órdenes.")},
    # {}: balance, equity, available (pre-formatted numbers)
    "balance": {
        "ko": "파시피카 잔고 ${} · 자산 ${} · 가용 ${}",
        "en": "Pacifica balance ${} · equity ${} · available ${}",
        "zh": "Pacifica 余额 ${} · 总资产 ${} · 可用 ${}",
        "ja": "Pacifica 残高 ${} · 資産 ${} · 利用可能 ${}",
        "vi": "Số dư Pacifica ${} · tài sản ${} · khả dụng ${}",
        "hi": "Pacifica बैलेंस ${} · इक्विटी ${} · उपलब्ध ${}",
        "id": "Saldo Pacifica ${} · ekuitas ${} · tersedia ${}",
        "ru": "Баланс Pacifica ${} · капитал ${} · доступно ${}",
        "pt": "Saldo Pacifica ${} · patrimônio ${} · disponível ${}",
        "tr": "Pacifica bakiyesi ${} · varlık ${} · kullanılabilir ${}",
        "es": "Saldo de Pacifica ${} · patrimonio ${} · disponible ${}"},
    "trades_header": {
        "ko": "최근 체결:", "en": "Recent fills:", "zh": "最近成交：",
        "ja": "直近の約定:", "vi": "Khớp lệnh gần đây:", "hi": "हाल की ट्रेड:",
        "id": "Transaksi terakhir:", "ru": "Последние сделки:",
        "pt": "Execuções recentes:", "tr": "Son işlemler:",
        "es": "Operaciones recientes:"},
    "trades_none": {
        "ko": "없음", "en": "none", "zh": "无", "ja": "なし",
        "vi": "không có", "hi": "कोई नहीं", "id": "tidak ada", "ru": "нет",
        "pt": "nenhuma", "tr": "yok", "es": "ninguna"},
    # {}: symbol, side, amount, price, pnl
    "trade_row": {
        "ko": "{} {} {} @ {} 손익 {}", "en": "{} {} {} @ {} PnL {}",
        "zh": "{} {} {} @ {} 盈亏 {}", "ja": "{} {} {} @ {} 損益 {}",
        "vi": "{} {} {} @ {} PnL {}", "hi": "{} {} {} @ {} PnL {}",
        "id": "{} {} {} @ {} PnL {}", "ru": "{} {} {} @ {} PnL {}",
        "pt": "{} {} {} @ {} PnL {}", "tr": "{} {} {} @ {} PnL {}",
        "es": "{} {} {} @ {} PnL {}"},
    "sym_none": {
        "ko": "{}: 파시피카에 없습니다", "en": "{}: not listed on Pacifica",
        "zh": "{}：Pacifica 上没有该品种", "ja": "{}: Pacifica にはありません",
        "vi": "{}: không có trên Pacifica",
        "hi": "{}: Pacifica पर उपलब्ध नहीं है",
        "id": "{}: tidak ada di Pacifica", "ru": "{}: нет на Pacifica",
        "pt": "{}: não está na Pacifica", "tr": "{}: Pacifica'da yok",
        "es": "{}: no está en Pacifica"},
    # {}: symbol, price, 24h change, funding APR, funding side, OI, volume
    "sym_info": {
        "ko": "{} 지금 {} · 24시간 {}%\n펀딩 연 {} ({})\n"
              "미결제 ${} · 24시간 거래 ${}",
        "en": "{} now {} · 24h {}%\nFunding APR {} ({})\n"
              "Open interest ${} · 24h volume ${}",
        "zh": "{} 现价 {} · 24小时 {}%\n资金费年化 {}（{}）\n"
              "未平仓 ${} · 24小时成交 ${}",
        "ja": "{} 現在 {} · 24時間 {}%\nファンディング年率 {}（{}）\n"
              "未決済 ${} · 24時間出来高 ${}",
        "vi": "{} hiện tại {} · 24 giờ {}%\nFunding năm {} ({})\n"
              "Hợp đồng mở ${} · khối lượng 24 giờ ${}",
        "hi": "{} अभी {} · 24 घंटे {}%\nफंडिंग वार्षिक {} ({})\n"
              "ओपन इंटरेस्ट ${} · 24 घंटे वॉल्यूम ${}",
        "id": "{} sekarang {} · 24 jam {}%\nFunding tahunan {} ({})\n"
              "Open interest ${} · volume 24 jam ${}",
        "ru": "{} сейчас {} · 24 ч {}%\nФандинг годовых {} ({})\n"
              "Открытый интерес ${} · объём за 24 ч ${}",
        "pt": "{} agora {} · 24h {}%\nFunding anual {} ({})\n"
              "Contratos em aberto ${} · volume 24h ${}",
        "tr": "{} şu an {} · 24 saat {}%\nYıllık fonlama {} ({})\n"
              "Açık pozisyon ${} · 24 saat hacim ${}",
        "es": "{} ahora {} · 24h {}%\nFunding anual {} ({})\n"
              "Interés abierto ${} · volumen 24h ${}"},
    "fund_long": {
        "ko": "숏이 롱에게 지불, 롱 수취",
        "en": "shorts pay longs, longs receive",
        "zh": "空头付给多头，多头收取",
        "ja": "ショートがロングに支払い、ロングが受取",
        "vi": "short trả cho long, long nhận",
        "hi": "शॉर्ट लॉन्ग को भुगतान करते हैं, लॉन्ग को मिलता है",
        "id": "short membayar long, long menerima",
        "ru": "шорты платят лонгам, лонги получают",
        "pt": "shorts pagam longs, longs recebem",
        "tr": "shortlar longlara öder, long alır",
        "es": "los cortos pagan a los largos, los largos cobran"},
    "fund_short": {
        "ko": "롱이 숏에게 지불, 숏 수취",
        "en": "longs pay shorts, shorts receive",
        "zh": "多头付给空头，空头收取",
        "ja": "ロングがショートに支払い、ショートが受取",
        "vi": "long trả cho short, short nhận",
        "hi": "लॉन्ग शॉर्ट को भुगतान करते हैं, शॉर्ट को मिलता है",
        "id": "long membayar short, short menerima",
        "ru": "лонги платят шортам, шорты получают",
        "pt": "longs pagam shorts, shorts recebem",
        "tr": "longlar shortlara öder, short alır",
        "es": "los largos pagan a los cortos, los cortos cobran"},
    "why_none": {
        "ko": "{} 체결 이력이 없습니다", "en": "{}: no trade history",
        "zh": "{} 没有成交记录", "ja": "{} の約定履歴がありません",
        "vi": "{}: chưa có lịch sử giao dịch",
        "hi": "{}: कोई ट्रेड इतिहास नहीं है",
        "id": "{}: belum ada riwayat transaksi",
        "ru": "{}: истории сделок нет",
        "pt": "{}: sem histórico de operações",
        "tr": "{}: işlem geçmişi yok",
        "es": "{}: sin historial de operaciones"},
    "why_noclose": {
        "ko": "{}: 아직 청산된 거래가 없습니다",
        "en": "{}: no closed trade yet",
        "zh": "{}：还没有已平仓的交易",
        "ja": "{}: まだ決済された取引がありません",
        "vi": "{}: chưa có giao dịch nào được đóng",
        "hi": "{}: अभी तक कोई ट्रेड बंद नहीं हुआ",
        "id": "{}: belum ada transaksi yang ditutup",
        "ru": "{}: закрытых сделок пока нет",
        "pt": "{}: nenhuma operação fechada ainda",
        "tr": "{}: henüz kapanan işlem yok",
        "es": "{}: aún no hay operaciones cerradas"},
    # {}: symbol, entry, exit, pct, pnl
    "why_line": {
        "ko": "{} 직전 거래: 진입 {} → 청산 {} ({}%) · 손익 {}",
        "en": "{} last trade: entry {} → exit {} ({}%) · PnL {}",
        "zh": "{} 上一笔交易：入场 {} → 平仓 {}（{}%）· 盈亏 {}",
        "ja": "{} 直近の取引: エントリー {} → 決済 {} ({}%) · 損益 {}",
        "vi": "{} giao dịch gần nhất: vào {} → thoát {} ({}%) · PnL {}",
        "hi": "{} पिछला ट्रेड: प्रवेश {} → निकास {} ({}%) · PnL {}",
        "id": "{} transaksi terakhir: masuk {} → keluar {} ({}%) · PnL {}",
        "ru": "{} последняя сделка: вход {} → выход {} ({}%) · PnL {}",
        "pt": "{} última operação: entrada {} → saída {} ({}%) · PnL {}",
        "tr": "{} son işlem: giriş {} → çıkış {} ({}%) · PnL {}",
        "es": "{} última operación: entrada {} → salida {} ({}%) · PnL {}"},
    "why_won": {
        "ko": "익절가에 먼저 닿아 이겼습니다",
        "en": "Won: the take-profit was touched first",
        "zh": "止盈价先被触及，因此获利",
        "ja": "先に利確価格に到達して勝ちました",
        "vi": "Thắng: mức chốt lời bị chạm trước",
        "hi": "जीत: टेक-प्रॉफिट पहले छुआ गया",
        "id": "Menang: take-profit tersentuh lebih dulu",
        "ru": "Победа: тейк-профит сработал первым",
        "pt": "Ganhou: o take-profit foi atingido primeiro",
        "tr": "Kazandı: önce kâr-al seviyesine ulaşıldı",
        "es": "Ganó: el take-profit se alcanzó primero"},
    "why_lost": {
        "ko": "손절/만기로 밀렸습니다",
        "en": "Lost: pushed out by the stop or expiry",
        "zh": "被止损/到期出局",
        "ja": "損切り/期限で押し出されました",
        "vi": "Thua: bị đẩy ra bởi cắt lỗ hoặc hết hạn",
        "hi": "हार: स्टॉप या समाप्ति से बाहर हुए",
        "id": "Kalah: terdorong keluar oleh stop atau kedaluwarsa",
        "ru": "Проигрыш: выбило по стопу или истечению",
        "pt": "Perdeu: saiu por stop ou vencimento",
        "tr": "Kaybetti: stop veya vade ile çıkıldı",
        "es": "Perdió: salió por stop o vencimiento"},
    "why_coin": {
        "ko": "방향 자체는 측정상 동전 던지기 수준이고, 결과는 어느 쪽 "
              "가격에 먼저 닿았는지로 갈립니다.",
        "en": "The direction itself measures like a coin flip; the outcome "
              "is decided by which price level is touched first.",
        "zh": "方向本身经测算近似掷硬币，结果取决于先触及哪一侧的价格。",
        "ja": "方向自体は測定上コイントス並みで、結果はどちらの価格に先に"
              "届いたかで決まります。",
        "vi": "Bản thân hướng đi, theo đo lường, chỉ như tung đồng xu; kết "
              "quả do mức giá nào bị chạm trước quyết định.",
        "hi": "दिशा स्वयं माप में सिक्का उछालने जैसी है; परिणाम इस पर निर्भर है "
              "कि कौन सा मूल्य स्तर पहले छुआ जाता है।",
        "id": "Arah itu sendiri, menurut pengukuran, seperti lempar koin; "
              "hasilnya ditentukan oleh level harga mana yang tersentuh "
              "lebih dulu.",
        "ru": "Само направление, по измерениям, как подбрасывание монеты; "
              "исход решает то, какой уровень цены достигнут первым.",
        "pt": "A direção em si, pelas medições, é como cara ou coroa; o "
              "resultado depende de qual nível de preço é atingido "
              "primeiro.",
        "tr": "Yönün kendisi ölçümlere göre yazı tura gibidir; sonucu hangi "
              "fiyat seviyesine önce ulaşıldığı belirler.",
        "es": "La dirección en sí, según las mediciones, es como lanzar una "
              "moneda; el resultado depende de qué nivel de precio se toca "
              "primero."},
    "bot_header": {
        "ko": "봇 최근 기록:", "en": "Bot recent log:",
        "zh": "机器人最近记录：", "ja": "ボットの最近の記録:",
        "vi": "Nhật ký gần đây của bot:", "hi": "बॉट का हालिया लॉग:",
        "id": "Log terbaru bot:", "ru": "Последние записи бота:",
        "pt": "Registro recente do bot:", "tr": "Botun son kayıtları:",
        "es": "Registro reciente del bot:"},
    "not_understood": {
        "ko": "이해하지 못했습니다. 이렇게 물어보실 수 있습니다:",
        "en": "I did not understand. You can ask like this:",
        "zh": "没有理解您的意思。可以这样提问：",
        "ja": "理解できませんでした。次のように質問できます:",
        "vi": "Tôi chưa hiểu. Bạn có thể hỏi như sau:",
        "hi": "समझ नहीं पाया। आप इस तरह पूछ सकते हैं:",
        "id": "Saya tidak mengerti. Anda bisa bertanya seperti ini:",
        "ru": "Не понял. Можно спросить так:",
        "pt": "Não entendi. Você pode perguntar assim:",
        "tr": "Anlayamadım. Şöyle sorabilirsiniz:",
        "es": "No entendí. Puede preguntar así:"},
    # {}: relative file path
    "file_missing": {
        "ko": "({} 없음, 수집기가 아직 만들지 않았습니다)",
        "en": "({} missing, the collector has not created it yet)",
        "zh": "（{} 不存在，采集器尚未生成）",
        "ja": "（{} がありません。収集プロセスが未作成です）",
        "vi": "({} chưa có, bộ thu thập chưa tạo tệp này)",
        "hi": "({} मौजूद नहीं, संग्राहक ने अभी इसे नहीं बनाया)",
        "id": "({} belum ada, pengumpul belum membuatnya)",
        "ru": "({} отсутствует, сборщик ещё не создал файл)",
        "pt": "({} não existe, o coletor ainda não o criou)",
        "tr": "({} yok, toplayıcı henüz oluşturmadı)",
        "es": "({} no existe, el recolector aún no lo ha creado)"},
    # {}: exception class name
    "error": {
        "ko": "오류: {}", "en": "Error: {}", "zh": "错误：{}",
        "ja": "エラー: {}", "vi": "Lỗi: {}", "hi": "त्रुटि: {}",
        "id": "Kesalahan: {}", "ru": "Ошибка: {}", "pt": "Erro: {}",
        "tr": "Hata: {}", "es": "Error: {}"},
    # Small-talk replies for the rule tier, in every menu language.
    "greet_reply": {
        "ko": "안녕하세요! 픽 순위, 잔고, 봇 상태, 펀딩 같은 걸 물어보시면 "
              "실데이터로 답해드립니다. /menu 를 치면 전체 목록이 나옵니다.",
        "en": "Hello! Ask me about picks, balance, bot status or funding "
              "and I answer from live data. /menu shows everything.",
        "zh": "你好！可以问我推荐币、余额、机器人状态、资金费等，我用实时数据"
              "回答。/menu 查看全部功能。",
        "ja": "こんにちは！ピック、残高、ボット状態、ファンディングなど聞いて"
              "ください。実データで答えます。/menu で全機能が見られます。",
        "vi": "Xin chào! Hãy hỏi về pick, số dư, trạng thái bot, funding — "
              "tôi trả lời bằng dữ liệu thực. /menu xem tất cả.",
        "hi": "नमस्ते! पिक, बैलेंस, बॉट स्थिति, फंडिंग के बारे में पूछें — मैं लाइव डेटा से "
              "जवाब देता हूँ। /menu सब दिखाता है।",
        "id": "Halo! Tanyakan pick, saldo, status bot, funding — saya jawab "
              "dengan data langsung. /menu menampilkan semuanya.",
        "ru": "Здравствуйте! Спросите про пики, баланс, статус бота, "
              "фандинг — отвечу по живым данным. /menu покажет всё.",
        "pt": "Olá! Pergunte sobre picks, saldo, status do bot, funding — "
              "respondo com dados ao vivo. /menu mostra tudo.",
        "tr": "Merhaba! Pick, bakiye, bot durumu, funding sorabilirsiniz — "
              "canlı veriyle yanıtlarım. /menu hepsini gösterir.",
        "es": "¡Hola! Pregunta por picks, saldo, estado del bot, funding — "
              "respondo con datos en vivo. /menu muestra todo."},
    "thanks_reply": {
        "ko": "감사합니다! 더 궁금한 게 있으면 언제든 물어보세요.",
        "en": "Thank you! Ask me anything else anytime.",
        "zh": "谢谢！有其他问题随时问我。",
        "ja": "ありがとうございます！他に気になることがあればいつでもどうぞ。",
        "vi": "Cảm ơn! Cứ hỏi tôi bất cứ lúc nào.",
        "hi": "धन्यवाद! कुछ और पूछना हो तो कभी भी पूछें।",
        "id": "Terima kasih! Silakan bertanya kapan saja.",
        "ru": "Спасибо! Обращайтесь в любое время.",
        "pt": "Obrigado! Pergunte quando quiser.",
        "tr": "Teşekkürler! İstediğiniz zaman sorabilirsiniz.",
        "es": "¡Gracias! Pregunta cuando quieras."},
    "alerts_info": {
        "ko": "알림 안내: 이 봇이 보내는 알림은 체결·경고 같은 상태 알림입니다. "
              "조언성 경고를 끄려면 policy.yaml 에 "
              "bracket_advisory_alerts: false 를 넣으면 됩니다 (로그에는 "
              "계속 남습니다). 체결 알림은 끄지 않는 걸 권장합니다.",
        "en": "About alerts: this bot sends status alerts (fills, warnings). "
              "To silence advisory warnings put bracket_advisory_alerts: "
              "false in policy.yaml (they stay in the log). Fill alerts are "
              "best left on.",
        "zh": "提醒说明：本机器人发送成交·警告等状态提醒。要关闭建议型警告，"
              "在 policy.yaml 中加入 bracket_advisory_alerts: false（日志仍"
              "保留）。成交提醒建议保持开启。",
        "ja": "通知について：このボットは約定・警告などの状態通知を送ります。"
              "助言的警告を止めるには policy.yaml に bracket_advisory_alerts:"
              " false を追加してください（ログには残ります）。約定通知はオン"
              "推奨です。",
        "vi": "Về cảnh báo: bot gửi thông báo trạng thái (khớp lệnh, cảnh "
              "báo). Tắt cảnh báo tư vấn bằng bracket_advisory_alerts: false "
              "trong policy.yaml (log vẫn giữ). Nên giữ thông báo khớp lệnh.",
        "hi": "सूचनाएँ: यह बॉट स्थिति सूचनाएँ भेजता है (फिल, चेतावनी)। सलाह-चेतावनी बंद "
              "करने के लिए policy.yaml में bracket_advisory_alerts: false डालें "
              "(लॉग में रहेंगी)। फिल सूचनाएँ चालू रखें।",
        "id": "Tentang notifikasi: bot mengirim status (eksekusi, "
              "peringatan). Matikan peringatan saran dengan "
              "bracket_advisory_alerts: false di policy.yaml (tetap di log). "
              "Notifikasi eksekusi sebaiknya tetap aktif.",
        "ru": "Об уведомлениях: бот шлёт статусные оповещения (сделки, "
              "предупреждения). Советные отключаются строкой "
              "bracket_advisory_alerts: false в policy.yaml (в логе "
              "остаются). Оповещения о сделках лучше оставить.",
        "pt": "Sobre alertas: o bot envia alertas de status (execuções, "
              "avisos). Para silenciar avisos consultivos use "
              "bracket_advisory_alerts: false no policy.yaml (ficam no "
              "log). Mantenha os alertas de execução.",
        "tr": "Bildirimler: bot durum bildirimleri gönderir (işlem, uyarı). "
              "Tavsiye uyarılarını kapatmak için policy.yaml'a "
              "bracket_advisory_alerts: false ekleyin (logda kalır). İşlem "
              "bildirimlerini açık tutun.",
        "es": "Sobre alertas: el bot envía alertas de estado (ejecuciones, "
              "avisos). Para silenciar avisos consultivos ponga "
              "bracket_advisory_alerts: false en policy.yaml (quedan en el "
              "log). Mantenga las alertas de ejecución."},
    "help_reply": {
        "ko": "저는 고정된 조회 질문에 실데이터로 답하는 무료 모드입니다. "
              "자유로운 대화는 유료 모드(본인 AI 키 등록, /mode)에서 됩니다.\n"
              "무료로 물어볼 수 있는 것: 픽 순위(/pick), 펀딩(/funding), "
              "캐리(/carry), 봇 상태(/bot), 잔고(/balance), 체결(/trades), "
              "종목명(예: 비트코인), \"왜 잃었어 SOL\" 같은 질문.",
        "en": "I am the free tier: fixed lookup questions answered from "
              "live data. Free-form conversation needs the paid tier (your "
              "own AI key, /mode).\nFree questions: /pick /funding /carry "
              "/bot /balance /trades, a symbol name, or \"why did SOL "
              "lose\".",
        "zh": "我是免费模式：用实时数据回答固定查询。自由对话需要付费模式"
              "（注册您自己的 AI 密钥，/mode）。\n可以问：/pick /funding "
              "/carry /bot /balance /trades、币种名，或\"SOL 为什么亏了\"。",
        "ja": "私は無料モードです：実データで定型の照会に答えます。自由会話は"
              "有料モード（ご自身の AI キー登録、/mode）で。\n聞けること："
              "/pick /funding /carry /bot /balance /trades、銘柄名、"
              "「SOL はなぜ負けた」など。",
        "vi": "Tôi là chế độ miễn phí: trả lời câu hỏi tra cứu bằng dữ liệu "
              "thực. Trò chuyện tự do cần chế độ trả phí (khóa AI của bạn, "
              "/mode).\nCó thể hỏi: /pick /funding /carry /bot /balance "
              "/trades, tên coin, hoặc \"vì sao SOL thua\".",
        "hi": "मैं मुफ्त मोड हूँ: लाइव डेटा से तय सवालों के जवाब। खुली बातचीत के लिए "
              "सशुल्क मोड चाहिए (अपनी AI कुंजी, /mode)।\nपूछें: /pick /funding "
              "/carry /bot /balance /trades, कोई सिंबल, या \"SOL क्यों हारा\"।",
        "id": "Saya tier gratis: menjawab pertanyaan tetap dari data "
              "langsung. Percakapan bebas butuh tier berbayar (kunci AI "
              "Anda, /mode).\nTanyakan: /pick /funding /carry /bot /balance "
              "/trades, nama koin, atau \"kenapa SOL kalah\".",
        "ru": "Я бесплатный режим: отвечаю на типовые запросы по живым "
              "данным. Свободный диалог — в платном режиме (ваш ключ ИИ, "
              "/mode).\nСпросите: /pick /funding /carry /bot /balance "
              "/trades, тикер или «почему SOL проиграл».",
        "pt": "Sou o nível gratuito: perguntas fixas com dados ao vivo. "
              "Conversa livre exige o nível pago (sua chave de IA, /mode).\n"
              "Pergunte: /pick /funding /carry /bot /balance /trades, um "
              "símbolo, ou \"por que SOL perdeu\".",
        "tr": "Ben ücretsiz katmanım: sabit sorguları canlı veriyle "
              "yanıtlarım. Serbest sohbet ücretli katman ister (kendi AI "
              "anahtarınız, /mode).\nSorun: /pick /funding /carry /bot "
              "/balance /trades, sembol adı veya \"SOL neden kaybetti\".",
        "es": "Soy el nivel gratuito: respondo consultas fijas con datos en "
              "vivo. La conversación libre requiere el nivel de pago (su "
              "clave de IA, /mode).\nPregunte: /pick /funding /carry /bot "
              "/balance /trades, un símbolo, o \"por qué perdió SOL\"."},
    # Execution flow (operator only; members get exec_member_no)
    "exec_confirm_on": {
        "ko": "자동매매(브래킷 봇)를 시작할까요? 실제 주문이 나갑니다.\n"
              "'예'라고 답하면 시작, '아니'면 취소합니다. (2분 내)",
        "en": "Start auto trading (bracket bot)? Real orders will be "
              "placed.\nReply 'yes' to start, 'no' to cancel. (2 min)",
        "zh": "启动自动交易（bracket 机器人）？将会真实下单。\n回复 yes 开始，"
              "no 取消。（2分钟内）",
        "ja": "自動売買（ブラケットボット）を開始しますか？実際に注文が出ます。"
              "\n「yes」で開始、「no」でキャンセル。（2分以内）",
        "vi": "Bắt đầu giao dịch tự động (bot bracket)? Lệnh thật sẽ được "
              "đặt.\nTrả lời 'yes' để bắt đầu, 'no' để hủy. (2 phút)",
        "hi": "ऑटो ट्रेडिंग (ब्रैकेट बॉट) शुरू करें? असली ऑर्डर जाएँगे।\n'yes' से शुरू, "
              "'no' से रद्द। (2 मिनट)",
        "id": "Mulai auto trading (bot bracket)? Order sungguhan akan "
              "dipasang.\nBalas 'yes' untuk mulai, 'no' untuk batal. (2 mnt)",
        "ru": "Запустить автоторговлю (bracket-бот)? Будут выставлены "
              "реальные ордера.\nОтветьте «yes» для запуска, «no» для "
              "отмены. (2 мин)",
        "pt": "Iniciar auto trading (bot bracket)? Ordens reais serão "
              "enviadas.\nResponda 'yes' para iniciar, 'no' para cancelar.",
        "tr": "Otomatik işlemi başlat (bracket bot)? Gerçek emirler "
              "verilecek.\n'yes' başlatır, 'no' iptal eder. (2 dk)",
        "es": "¿Iniciar auto trading (bot bracket)? Se enviarán órdenes "
              "reales.\nResponda 'yes' para iniciar, 'no' para cancelar."},
    "exec_confirm_off": {
        "ko": "자동매매를 중지할까요? 열려 있는 포지션은 거래소의 익절·손절이 "
              "계속 지킵니다.\n'예'면 중지, '아니'면 취소합니다. (2분 내)",
        "en": "Stop auto trading? Open positions stay protected by the "
              "exchange-side TP/SL.\nReply 'yes' to stop, 'no' to cancel.",
        "zh": "停止自动交易？已开仓位仍由交易所止盈止损保护。\nyes 停止，"
              "no 取消。",
        "ja": "自動売買を停止しますか？保有ポジションは取引所側の TP/SL が"
              "守り続けます。\n「yes」で停止、「no」でキャンセル。",
        "vi": "Dừng giao dịch tự động? Vị thế mở vẫn được TP/SL của sàn bảo "
              "vệ.\n'yes' dừng, 'no' hủy.",
        "hi": "ऑटो ट्रेडिंग रोकें? खुली पोजीशन एक्सचेंज TP/SL से सुरक्षित रहेंगी।\n'yes' "
              "रोकें, 'no' रद्द।",
        "id": "Hentikan auto trading? Posisi terbuka tetap dilindungi TP/SL "
              "bursa.\n'yes' berhenti, 'no' batal.",
        "ru": "Остановить автоторговлю? Открытые позиции защищает биржевой "
              "TP/SL.\n«yes» — остановить, «no» — отмена.",
        "pt": "Parar o auto trading? Posições abertas seguem protegidas "
              "pelo TP/SL da corretora.\n'yes' para, 'no' cancela.",
        "tr": "Otomatik işlemi durdur? Açık pozisyonları borsa TP/SL "
              "korumaya devam eder.\n'yes' durdurur, 'no' iptal.",
        "es": "¿Detener auto trading? Las posiciones abiertas siguen "
              "protegidas por TP/SL del exchange.\n'yes' detiene, 'no' "
              "cancela."},
    "exec_done_on": {
        "ko": "시작 명령을 보냈습니다. 안전가드(직전 봇 흔적 10분)에 걸리면 "
              "거부될 수 있으니 잠시 후 /bot 으로 확인하세요.",
        "en": "Start command sent. The safety guard (10-min trace of the "
              "previous bot) may refuse; check /bot in a moment.",
        "zh": "已发送启动命令。安全防护（前一个机器人的10分钟痕迹）可能拒绝，"
              "请稍后用 /bot 确认。",
        "ja": "開始コマンドを送りました。安全ガード（直前ボットの10分の痕跡）"
              "で拒否される場合があります。/bot で確認してください。",
        "vi": "Đã gửi lệnh khởi động. Bộ bảo vệ (dấu vết bot trước 10 phút) "
              "có thể từ chối; kiểm tra /bot.",
        "hi": "स्टार्ट कमांड भेजा। सेफ्टी गार्ड (पिछले बॉट का 10-मिनट निशान) मना कर सकता "
              "है; /bot देखें।",
        "id": "Perintah mulai dikirim. Pengaman (jejak 10 menit bot "
              "sebelumnya) bisa menolak; cek /bot.",
        "ru": "Команда запуска отправлена. Защита (10-минутный след "
              "прежнего бота) может отказать; проверьте /bot.",
        "pt": "Comando de início enviado. A proteção (rastro de 10 min do "
              "bot anterior) pode recusar; veja /bot.",
        "tr": "Başlatma komutu gönderildi. Güvenlik koruması (önceki botun "
              "10 dk izi) reddedebilir; /bot ile bakın.",
        "es": "Comando de inicio enviado. La protección (rastro de 10 min "
              "del bot anterior) puede rechazarlo; revise /bot."},
    # Failure has to be as loud as success: a user who asked to stop and was
    # told "stopped" while it kept trading is the worst outcome this file
    # can produce. {pids} is filled in by the caller. (2026-08-26)
    "exec_stop_failed": {
        "ko": "자동매매를 여러 번 강제 종료했지만 아직 살아 있습니다 "
              "(PID {pids}). 잠시 뒤 다시 꺼달라고 말해 주세요. 그래도 "
              "안 되면 컴퓨터를 재시작하면 확실히 꺼집니다. 포지션은 "
              "거래소 익절·손절이 계속 지킵니다.",
        "en": "Force stopped it several times but it is still alive (PID "
              "{pids}). Ask me to stop it again in a moment. If it still "
              "will not stop, restarting the computer always works. Your "
              "positions stay protected by the exchange TP/SL.",
        "zh": "已多次强制停止，但仍在运行（PID {pids}）。请稍后再让我停止一次。"
              "若仍无效，重启电脑一定可以停止。交易所止盈止损继续保护仓位。",
        "ja": "強制終了を数回試しましたがまだ動いています (PID {pids})。少し"
              "経ってからもう一度停止と伝えてください。それでも止まらない"
              "場合はパソコンの再起動で確実に止まります。取引所の TP/SL は"
              "ポジションを守り続けます。",
        "vi": "Đã buộc dừng nhiều lần nhưng vẫn chạy (PID {pids}). Lát nữa "
              "hãy bảo tôi dừng lại lần nữa. Nếu vẫn không được, khởi động "
              "lại máy tính chắc chắn dừng. TP/SL của sàn vẫn bảo vệ vị thế.",
        "hi": "कई बार ज़बरदस्ती बंद किया पर अब भी चल रहा है (PID {pids})। थोड़ी देर "
              "बाद फिर बंद करने को कहें। तब भी न रुके तो कंप्यूटर रीस्टार्ट करने से "
              "ज़रूर रुकेगा। एक्सचेंज TP/SL आपकी पोज़िशन बचाता रहेगा।",
        "id": "Sudah dipaksa berhenti beberapa kali tapi masih jalan (PID "
              "{pids}). Minta saya hentikan lagi sebentar lagi. Kalau tetap "
              "tidak bisa, restart komputer pasti berhasil. TP/SL bursa "
              "tetap melindungi posisi Anda.",
        "ru": "Несколько раз принудительно остановил, но процесс жив (PID "
              "{pids}). Попросите остановить ещё раз чуть позже. Если не "
              "поможет, перезагрузка компьютера остановит точно. Биржевой "
              "TP/SL продолжает защищать позиции.",
        "pt": "Forcei a parada várias vezes e ainda está ativo (PID {pids}). "
              "Peça para eu parar de novo daqui a pouco. Se ainda assim não "
              "parar, reiniciar o computador resolve. O TP/SL da corretora "
              "continua protegendo suas posições.",
        "tr": "Birkaç kez zorla durdurdum ama hâlâ çalışıyor (PID {pids}). "
              "Biraz sonra tekrar durdurmamı isteyin. Yine durmazsa "
              "bilgisayarı yeniden başlatmak kesin çözümdür. Borsa TP/SL "
              "pozisyonlarınızı korumaya devam ediyor.",
        "es": "Lo forcé a detenerse varias veces y sigue activo (PID "
              "{pids}). Pídame detenerlo otra vez en un momento. Si aun así "
              "no para, reiniciar el ordenador siempre funciona. El TP/SL "
              "del exchange sigue protegiendo sus posiciones."},
    "exec_done_off": {
        "ko": "자동매매를 중지했고, 꺼진 것까지 확인했습니다. 포지션 보호"
              "(거래소 익절·손절)는 그대로 살아 있습니다. /bot 으로 확인하세요.",
        "en": "Auto trading stopped, and verified stopped. Exchange-side "
              "TP/SL protection stays on. Check /bot.",
        "zh": "自动交易已停止。交易所止盈止损仍然有效。请用 /bot 确认。",
        "ja": "自動売買を停止しました。取引所側 TP/SL は有効のままです。"
              "/bot で確認を。",
        "vi": "Đã dừng giao dịch tự động. TP/SL của sàn vẫn hoạt động. /bot.",
        "hi": "ऑटो ट्रेडिंग रुकी। एक्सचेंज TP/SL चालू है। /bot देखें।",
        "id": "Auto trading dihentikan. TP/SL bursa tetap aktif. Cek /bot.",
        "ru": "Автоторговля остановлена. Биржевой TP/SL действует. /bot.",
        "pt": "Auto trading parado. TP/SL da corretora segue ativo. /bot.",
        "tr": "Otomatik işlem durdu. Borsa TP/SL etkin. /bot.",
        "es": "Auto trading detenido. TP/SL del exchange sigue activo. "
              "/bot."},
    "exec_already_on": {
        "ko": "자동매매가 이미 돌고 있습니다. /bot 으로 상태를 보세요.",
        "en": "Auto trading is already running. See /bot.",
        "zh": "自动交易已在运行。用 /bot 查看状态。",
        "ja": "自動売買はすでに動いています。/bot で状態を。",
        "vi": "Auto trading đang chạy. Xem /bot.",
        "hi": "ऑटो ट्रेडिंग पहले से चल रही है। /bot देखें।",
        "id": "Auto trading sudah berjalan. Lihat /bot.",
        "ru": "Автоторговля уже работает. См. /bot.",
        "pt": "O auto trading já está rodando. Veja /bot.",
        "tr": "Otomatik işlem zaten çalışıyor. /bot.",
        "es": "El auto trading ya está en marcha. Vea /bot."},
    "exec_already_off": {
        "ko": "지금 자동매매가 돌고 있지 않습니다.",
        "en": "Auto trading is not running now.",
        "zh": "自动交易当前未运行。",
        "ja": "自動売買は現在動いていません。",
        "vi": "Auto trading hiện không chạy.",
        "hi": "ऑटो ट्रेडिंग अभी नहीं चल रही।",
        "id": "Auto trading tidak sedang berjalan.",
        "ru": "Автоторговля сейчас не запущена.",
        "pt": "O auto trading não está rodando agora.",
        "tr": "Otomatik işlem şu an çalışmıyor.",
        "es": "El auto trading no está en marcha."},
    "exec_cancel": {
        "ko": "취소했습니다.", "en": "Cancelled.", "zh": "已取消。",
        "ja": "キャンセルしました。", "vi": "Đã hủy.", "hi": "रद्द किया।",
        "id": "Dibatalkan.", "ru": "Отменено.", "pt": "Cancelado.",
        "tr": "İptal edildi.", "es": "Cancelado."},
    "exec_member_no": {
        "ko": "이 방에서는 주문·매매 실행이 되지 않습니다 (지갑 주소만 등록된 "
              "조회 전용). 자동매매는 본인 컴퓨터에 ocean-agent 를 설치해 "
              "본인 키로 돌리는 방식입니다: pip install ocean-agent",
        "en": "Execution is not available here (address-only, read-only). "
              "Auto trading runs on YOUR machine with your own keys: "
              "pip install ocean-agent",
        "zh": "此处无法执行下单（仅注册地址，只读）。自动交易需在您自己的电脑"
              "上用自己的密钥运行：pip install ocean-agent",
        "ja": "ここでは注文・売買の実行はできません（アドレスのみ登録、照会"
              "専用）。自動売買はご自身の PC でご自身のキーで動かします："
              "pip install ocean-agent",
        "vi": "Không thể thực hiện lệnh ở đây (chỉ đăng ký địa chỉ, chỉ "
              "đọc). Giao dịch tự động chạy trên máy của bạn với khóa của "
              "bạn: pip install ocean-agent",
        "hi": "यहाँ ऑर्डर निष्पादन उपलब्ध नहीं (केवल पता, रीड-ओनली)। ऑटो ट्रेडिंग आपकी "
              "मशीन पर आपकी कुंजियों से चलती है: pip install ocean-agent",
        "id": "Eksekusi tidak tersedia di sini (hanya alamat, baca-saja). "
              "Auto trading berjalan di komputer Anda dengan kunci Anda: "
              "pip install ocean-agent",
        "ru": "Здесь исполнение недоступно (только адрес, режим чтения). "
              "Автоторговля работает на вашем компьютере с вашими ключами: "
              "pip install ocean-agent",
        "pt": "Execução não disponível aqui (somente endereço, apenas "
              "leitura). O auto trading roda na sua máquina com suas "
              "chaves: pip install ocean-agent",
        "tr": "Burada emir çalıştırılamaz (yalnızca adres, salt okunur). "
              "Otomatik işlem kendi bilgisayarınızda kendi anahtarlarınızla "
              "çalışır: pip install ocean-agent",
        "es": "La ejecución no está disponible aquí (solo dirección, solo "
              "lectura). El auto trading corre en su máquina con sus "
              "claves: pip install ocean-agent"},
    # {} {}: new version, current version
    "update_available": {
        "ko": "🔔 새 버전 {} 이 나왔습니다 (현재 {}). '업데이트'라고 답하면 "
              "자동으로 설치하고 재시작합니다. 자동매매 봇이 돌고 있다면 "
              "업데이트 후 '매매 꺼' → '매매 켜'로 새 버전을 태우세요.",
        "en": "🔔 Version {} is out (you run {}). Reply 'update' and I "
              "install it and restart myself. If the trading bot is "
              "running, stop and start it after the update.",
        "zh": "🔔 新版本 {} 已发布（当前 {}）。回复\"更新\"即可自动安装并重启。"
              "若交易机器人正在运行，更新后请先停止再启动。",
        "ja": "🔔 新バージョン {} が出ました（現在 {}）。「アップデート」と"
              "返信すると自動でインストールして再起動します。取引ボットが"
              "動作中なら、更新後に停止→起動してください。",
        "vi": "🔔 Phiên bản {} đã ra (hiện tại {}). Trả lời 'cập nhật' để "
              "tôi tự cài và khởi động lại. Nếu bot giao dịch đang chạy, "
              "hãy dừng rồi bật lại sau khi cập nhật.",
        "hi": "🔔 नया संस्करण {} आया है (वर्तमान {})। 'अपडेट' भेजें, मैं इंस्टॉल कर "
              "पुनः शुरू हो जाऊँगा। ट्रेडिंग बॉट चल रहा हो तो अपडेट के बाद बंद कर "
              "फिर चालू करें।",
        "id": "🔔 Versi {} telah rilis (sekarang {}). Balas 'update' dan "
              "saya pasang lalu mulai ulang sendiri. Jika bot trading "
              "berjalan, hentikan lalu nyalakan lagi setelah update.",
        "ru": "🔔 Вышла версия {} (у вас {}). Ответьте «обнови», и я "
              "установлю её и перезапущусь. Если торговый бот работает, "
              "после обновления остановите и запустите его.",
        "pt": "🔔 Saiu a versão {} (você usa {}). Responda 'atualiza' que "
              "eu instalo e reinicio sozinho. Se o bot de trading estiver "
              "rodando, pare e inicie após a atualização.",
        "tr": "🔔 Yeni sürüm {} çıktı (şu an {}). 'güncelle' yazın, kurup "
              "kendimi yeniden başlatayım. İşlem botu çalışıyorsa "
              "güncellemeden sonra durdurup açın.",
        "es": "🔔 Salió la versión {} (usas {}). Responde 'actualiza' y la "
              "instalo y me reinicio solo. Si el bot de trading está en "
              "marcha, deténlo y arráncalo tras actualizar."},
    "update_running": {
        "ko": "업데이트 설치 중...", "en": "Installing the update...",
        "zh": "正在安装更新...", "ja": "アップデートをインストール中...",
        "vi": "Đang cài bản cập nhật...", "hi": "अपडेट इंस्टॉल हो रहा है...",
        "id": "Memasang pembaruan...", "ru": "Устанавливаю обновление...",
        "pt": "Instalando a atualização...", "tr": "Güncelleme kuruluyor...",
        "es": "Instalando la actualización..."},
    "update_done": {
        "ko": "설치 완료. 새 버전으로 재시작합니다.",
        "en": "Installed. Restarting on the new version.",
        "zh": "安装完成，正在以新版本重启。",
        "ja": "インストール完了。新バージョンで再起動します。",
        "vi": "Đã cài xong. Khởi động lại với phiên bản mới.",
        "hi": "इंस्टॉल पूरा। नए संस्करण पर पुनः आरंभ।",
        "id": "Terpasang. Mulai ulang dengan versi baru.",
        "ru": "Установлено. Перезапускаюсь на новой версии.",
        "pt": "Instalado. Reiniciando na nova versão.",
        "tr": "Kuruldu. Yeni sürümle yeniden başlıyorum.",
        "es": "Instalado. Reiniciando con la nueva versión."},
    "update_failed": {
        "ko": "업데이트 실패. 터미널에서 직접: pip install -U ocean-agent",
        "en": "Update failed. Run manually: pip install -U ocean-agent",
        "zh": "更新失败。请手动执行：pip install -U ocean-agent",
        "ja": "更新に失敗。手動で: pip install -U ocean-agent",
        "vi": "Cập nhật thất bại. Chạy tay: pip install -U ocean-agent",
        "hi": "अपडेट विफल। मैन्युअल चलाएँ: pip install -U ocean-agent",
        "id": "Pembaruan gagal. Jalankan manual: pip install -U ocean-agent",
        "ru": "Не удалось обновиться. Вручную: pip install -U ocean-agent",
        "pt": "Falha na atualização. Rode: pip install -U ocean-agent",
        "tr": "Güncelleme başarısız. Elle: pip install -U ocean-agent",
        "es": "Falló la actualización. Ejecuta: pip install -U ocean-agent"},
    # {}: game, side, distance, leverages, actual APY, breakeven APY
    "order_ask_amount": {
        "ko": "{} {} · {}배로 주문 준비. 얼마(달러)를 넣을까요? 숫자로 답해주세요. 취소는 '아니'.",
        "en": "{} {} at {}x ready. How many dollars? Reply with a number; 'no' cancels.",
        "zh": "{} {} · {}倍已就绪。投入多少美元？请回复数字，'no' 取消。",
        "ja": "{} {} · {}倍で準備。何ドル入れますか？数字で返信、'no' でキャンセル。",
        "vi": "{} {} · {}x sẵn sàng. Bao nhiêu đô? Trả lời số; 'no' để hủy.",
        "hi": "{} {} · {}x तैयार। कितने डॉलर? संख्या भेजें; 'no' से रद्द।",
        "id": "{} {} · {}x siap. Berapa dolar? Balas angka; 'no' membatalkan.",
        "ru": "{} {} · {}x готово. Сколько долларов? Ответьте числом; «no» — отмена.",
        "pt": "{} {} · {}x pronto. Quantos dólares? Responda um número; 'no' cancela.",
        "tr": "{} {} · {}x hazır. Kaç dolar? Sayı yazın; 'no' iptal eder.",
        "es": "{} {} · {}x listo. ¿Cuántos dólares? Responde un número; 'no' cancela."},
    "order_confirm": {
        "ko": "주문 미리보기: {} {} ${} · {}배 · 익절 +{}% / 손절 -{}% (시장가 진입, 거래소 TP/SL 부착). 실행할까요? '예'로 실행, '아니'로 취소. (3분)",
        "en": "Order preview: {} {} ${} · {}x · TP +{}% / SL -{}% (market entry, exchange TP/SL attached). Execute? 'yes' runs, 'no' cancels. (3 min)",
        "zh": "订单预览：{} {} ${} · {}倍 · 止盈 +{}% / 止损 -{}%（市价，交易所TP/SL）。执行吗？yes 执行，no 取消。（3分钟）",
        "ja": "注文プレビュー：{} {} ${} · {}倍 · TP +{}% / SL -{}%（成行、取引所TP/SL付き）。実行しますか？「yes」実行、「no」キャンセル。（3分）",
        "vi": "Xem trước: {} {} ${} · {}x · TP +{}% / SL -{}% (thị trường, kèm TP/SL sàn). Thực hiện? 'yes' chạy, 'no' hủy. (3 phút)",
        "hi": "पूर्वावलोकन: {} {} ${} · {}x · TP +{}% / SL -{}% (मार्केट, एक्सचेंज TP/SL)। चलाएँ? 'yes' हाँ, 'no' रद्द। (3 मिनट)",
        "id": "Pratinjau: {} {} ${} · {}x · TP +{}% / SL -{}% (market, TP/SL bursa). Jalankan? 'yes' ya, 'no' batal. (3 mnt)",
        "ru": "Предпросмотр: {} {} ${} · {}x · TP +{}% / SL -{}% (маркет, биржевые TP/SL). Исполнить? «yes» — да, «no» — отмена. (3 мин)",
        "pt": "Prévia: {} {} ${} · {}x · TP +{}% / SL -{}% (mercado, TP/SL na corretora). Executar? 'yes' sim, 'no' cancela. (3 min)",
        "tr": "Önizleme: {} {} ${} · {}x · TP +{}% / SL -{}% (piyasa, borsa TP/SL). Çalıştır? 'yes' evet, 'no' iptal. (3 dk)",
        "es": "Vista previa: {} {} ${} · {}x · TP +{}% / SL -{}% (mercado, TP/SL del exchange). ¿Ejecutar? 'yes' sí, 'no' cancela. (3 min)"},
    "order_done": {
        "ko": "✅ 주문 완료: {} {} ${} · {}배 · 기준가 {} · 익절 {} / 손절 {} (거래소 부착)",
        "en": "✅ Order placed: {} {} ${} · {}x · ref {} · TP {} / SL {} (exchange-attached)",
        "zh": "✅ 已下单：{} {} ${} · {}倍 · 基准 {} · TP {} / SL {}",
        "ja": "✅ 注文完了：{} {} ${} · {}倍 · 基準 {} · TP {} / SL {}",
        "vi": "✅ Đã đặt: {} {} ${} · {}x · giá {} · TP {} / SL {}",
        "hi": "✅ ऑर्डर हुआ: {} {} ${} · {}x · मूल्य {} · TP {} / SL {}",
        "id": "✅ Order dibuat: {} {} ${} · {}x · harga {} · TP {} / SL {}",
        "ru": "✅ Ордер размещён: {} {} ${} · {}x · цена {} · TP {} / SL {}",
        "pt": "✅ Ordem enviada: {} {} ${} · {}x · preço {} · TP {} / SL {}",
        "tr": "✅ Emir verildi: {} {} ${} · {}x · fiyat {} · TP {} / SL {}",
        "es": "✅ Orden enviada: {} {} ${} · {}x · precio {} · TP {} / SL {}"},
    "order_fail": {
        "ko": "주문 실패: {}",
        "en": "Order failed: {}",
        "zh": "下单失败：{}",
        "ja": "注文失敗：{}",
        "vi": "Đặt lệnh thất bại: {}",
        "hi": "ऑर्डर विफल: {}",
        "id": "Order gagal: {}",
        "ru": "Ошибка ордера: {}",
        "pt": "Falha na ordem: {}",
        "tr": "Emir başarısız: {}",
        "es": "Orden fallida: {}"},
    "print_cycle_done": {
        "ko": "⏱ 프린트 24시간 종료: {} {} · 예치 ${} · 받은 프리미엄 ${}. 회수 완료, APR만 수령했습니다. 다시 걸까요? (예/아니)",
        "en": "⏱ Print 24h cycle ended: {} {} · deposit ${} · premium earned ${}. Withdrawn, APR collected. Re-enter? (yes/no)",
        "zh": "⏱ Print 24小时周期结束：{} {} · 存入 ${} · 已赚溢价 ${}。已提取，仅收取APR。要再次参与吗？(yes/no)",
        "ja": "⏱ Print 24時間サイクル終了: {} {} · 預入 ${} · プレミアム ${}。回収済み、APRのみ受領。再度入れますか？(yes/no)",
        "vi": "⏱ Chu kỳ Print 24h kết thúc: {} {} · gửi ${} · phí thu ${}. Đã rút, chỉ nhận APR. Vào lại? (yes/no)",
        "hi": "⏱ Print 24 घंटे का चक्र समाप्त: {} {} · जमा ${} · प्रीमियम ${}। निकासी पूर्ण, केवल APR लिया। फिर से लगाएं? (yes/no)",
        "id": "⏱ Siklus Print 24 jam selesai: {} {} · deposit ${} · premi ${}. Sudah ditarik, hanya APR diambil. Masuk lagi? (yes/no)",
        "ru": "⏱ Цикл Print 24ч завершён: {} {} · депозит ${} · премия ${}. Выведено, получен только APR. Зайти снова? (yes/no)",
        "pt": "⏱ Ciclo Print de 24h encerrado: {} {} · depósito ${} · prêmio ${}. Sacado, apenas APR coletado. Entrar de novo? (yes/no)",
        "tr": "⏱ Print 24s döngüsü bitti: {} {} · yatırılan ${} · prim ${}. Çekildi, yalnız APR alındı. Tekrar girilsin mi? (yes/no)",
        "es": "⏱ Ciclo Print de 24h terminado: {} {} · depósito ${} · prima ${}. Retirado, solo APR cobrado. ¿Entrar de nuevo? (yes/no)"},
    "print_cycle_fail": {
        "ko": "프린트 회수가 아직 안 됐습니다: {} (다음 시간에 자동 재시도합니다)",
        "en": "Print withdrawal not done yet: {} (will retry automatically next hour)",
        "zh": "Print 提取尚未完成：{}（下小时自动重试）",
        "ja": "Printの回収が未完了です: {}（次の1時間で自動再試行）",
        "vi": "Chưa rút được Print: {} (sẽ tự thử lại sau một giờ)",
        "hi": "Print निकासी अभी नहीं हुई: {} (अगले घंटे स्वतः पुनः प्रयास)",
        "id": "Penarikan Print belum selesai: {} (dicoba lagi otomatis jam berikutnya)",
        "ru": "Вывод Print ещё не выполнен: {} (автоповтор через час)",
        "pt": "Saque do Print ainda não feito: {} (nova tentativa automática na próxima hora)",
        "tr": "Print çekimi henüz olmadı: {} (bir saat sonra otomatik denenecek)",
        "es": "Retiro del Print aún no hecho: {} (reintento automático en una hora)"},
    "print_alert_hint": {
        "ko": "실행하려면 '프린트 실행'이라고 답하세요. 넘기려면 무시하면 됩니다.",
        "en": "Reply 'print execute' to take it, or ignore to pass.",
        "zh": "回复 'print execute' 即可执行，忽略则跳过。",
        "ja": "実行するには 'print execute' と返信してください。無視すればスキップします。",
        "vi": "Trả lời 'print execute' để thực hiện, bỏ qua nếu không muốn.",
        "hi": "लेने के लिए 'print execute' का जवाब दें, छोड़ने के लिए अनदेखा करें।",
        "id": "Balas 'print execute' untuk mengambil, abaikan untuk melewati.",
        "ru": "Ответьте 'print execute', чтобы исполнить, или игнорируйте.",
        "pt": "Responda 'print execute' para executar, ou ignore para pular.",
        "tr": "Almak için 'print execute' yazın, geçmek için yok sayın.",
        "es": "Responde 'print execute' para tomarlo, o ignora para pasar."},
    "print_combo": {
        "ko": "▶ {} {} 거리 {} · 가능 배수 {} · 실제 {} vs 본전 {}",
        "en": "▶ {} {} dist {} · leverages {} · actual {} vs breakeven {}",
        "zh": "▶ {} {} 距离 {} · 可用倍数 {} · 实际 {} vs 盈亏平衡 {}",
        "ja": "▶ {} {} 距離 {} · 可能倍率 {} · 実 {} vs 損益分岐 {}",
        "vi": "▶ {} {} k/c {} · đòn bẩy {} · thực {} vs hòa vốn {}",
        "hi": "▶ {} {} दूरी {} · लीवरेज {} · वास्तविक {} vs ब्रेक-ईवन {}",
        "id": "▶ {} {} jarak {} · leverage {} · aktual {} vs impas {}",
        "ru": "▶ {} {} дист. {} · плечи {} · факт {} vs безубыток {}",
        "pt": "▶ {} {} dist {} · alavancagens {} · real {} vs equilíbrio {}",
        "tr": "▶ {} {} mesafe {} · kaldıraçlar {} · gerçek {} vs başabaş {}",
        "es": "▶ {} {} dist {} · apalancamientos {} · real {} vs equilibrio {}"},
    "print_wait": {
        "ko": "⏳ 프린트 판정 계산 중입니다 (과거 데이터 전체 대조, 수십 초에서 몇 분). 끝나면 바로 보내드릴게요. 그동안 다른 질문은 계속 됩니다.",
        "en": "⏳ Computing the Print verdict (full history check, tens of seconds to a few minutes). I will send it when done; other questions keep working meanwhile.",
        "zh": "⏳ 正在计算 Print 判定（对照全部历史数据，数十秒到几分钟）。完成后立即发送，期间其他问题照常回答。",
        "ja": "⏳ Print 判定を計算中です（全履歴と照合、数十秒〜数分）。完了次第お送りします。その間も他の質問には答えられます。",
        "vi": "⏳ Đang tính phán định Print (đối chiếu toàn bộ lịch sử, vài chục giây đến vài phút). Xong sẽ gửi ngay; các câu hỏi khác vẫn hoạt động.",
        "hi": "⏳ Print निर्णय की गणना हो रही है (पूरा इतिहास, दसियों सेकंड से कुछ मिनट)। पूरा होते ही भेजूँगा; बाकी सवाल चलते रहेंगे।",
        "id": "⏳ Menghitung vonis Print (cek seluruh riwayat, puluhan detik hingga beberapa menit). Akan dikirim begitu selesai; pertanyaan lain tetap dilayani.",
        "ru": "⏳ Считаю вердикт по Print (сверка всей истории, от десятков секунд до пары минут). Пришлю, как закончу; остальные вопросы работают.",
        "pt": "⏳ Calculando o veredicto do Print (histórico completo, de dezenas de segundos a alguns minutos). Envio assim que terminar; outras perguntas continuam funcionando.",
        "tr": "⏳ Print kararı hesaplanıyor (tüm geçmişle karşılaştırma, onlarca saniye ile birkaç dakika). Bitince göndereceğim; diğer sorular çalışmaya devam eder.",
        "es": "⏳ Calculando el veredicto del Print (historial completo, de decenas de segundos a unos minutos). Lo envío al terminar; las demás preguntas siguen funcionando."},
    "print_yes": {
        "ko": "✅ 지금 잡을 만한 프린트가 있습니다 (실제 APY가 손익분기보다 높음). 아래 ✅ 줄을 보세요.",
        "en": "✅ There IS a print worth taking now (actual APY above breakeven). See the ✅ rows below.",
        "zh": "✅ 现在有值得参与的 Print（实际 APY 高于盈亏平衡）。请看下方 ✅ 行。",
        "ja": "✅ 今は取る価値のある Print があります（実 APY が損益分岐超え）。下の ✅ 行をご覧ください。",
        "vi": "✅ Hiện có Print đáng tham gia (APY thực cao hơn hòa vốn). Xem các dòng ✅ bên dưới.",
        "hi": "✅ अभी लेने लायक Print है (वास्तविक APY ब्रेक-ईवन से ऊपर)। नीचे ✅ पंक्तियाँ देखें।",
        "id": "✅ Ada Print yang layak diambil sekarang (APY aktual di atas impas). Lihat baris ✅ di bawah.",
        "ru": "✅ Сейчас есть Print, который стоит взять (фактический APY выше безубытка). См. строки ✅ ниже.",
        "pt": "✅ HÁ um Print que vale a pena agora (APY real acima do ponto de equilíbrio). Veja as linhas ✅ abaixo.",
        "tr": "✅ Şu an almaya değer bir Print VAR (gerçek APY başabaşın üstünde). Aşağıdaki ✅ satırlara bakın.",
        "es": "✅ HAY un Print que vale la pena ahora (APY real sobre el punto de equilibrio). Vea las filas ✅ abajo."},
    "print_no": {
        "ko": "지금은 잡을 만한 프린트가 없습니다. 모든 조합의 실제 APY가 손익분기(체결 위험값) 아래라, 걸수록 기대값이 마이너스입니다. 조건이 좋아지면 이 판정이 ✅로 바뀝니다.",
        "en": "No print is worth taking right now: every combination's actual APY sits below its breakeven (the fill-risk cost), so the expected value is negative. When conditions improve this verdict turns ✅.",
        "zh": "目前没有值得参与的 Print：所有组合的实际 APY 都低于盈亏平衡（成交风险成本），期望值为负。条件好转时此判定会变为 ✅。",
        "ja": "今は取る価値のある Print はありません。全組み合わせの実 APY が損益分岐（約定リスクコスト）を下回り、期待値はマイナスです。条件が良くなればこの判定は ✅ に変わります。",
        "vi": "Hiện không có Print đáng tham gia: APY thực của mọi tổ hợp đều dưới hòa vốn (chi phí rủi ro khớp), kỳ vọng âm. Khi điều kiện tốt hơn, phán định này sẽ thành ✅.",
        "hi": "अभी कोई Print लेने लायक नहीं: हर संयोजन का वास्तविक APY ब्रेक-ईवन (फिल जोखिम लागत) से नीचे है, अपेक्षित मूल्य ऋणात्मक। स्थिति सुधरने पर यह ✅ हो जाएगा।",
        "id": "Tidak ada Print yang layak sekarang: APY aktual semua kombinasi di bawah impas (biaya risiko eksekusi), nilai harapan negatif. Saat kondisi membaik, vonis ini menjadi ✅.",
        "ru": "Сейчас брать Print не стоит: фактический APY всех комбинаций ниже безубытка (цены риска исполнения), ожидание отрицательное. Когда условия улучшатся, вердикт станет ✅.",
        "pt": "Nenhum Print vale a pena agora: o APY real de todas as combinações está abaixo do equilíbrio (custo do risco de execução), valor esperado negativo. Quando melhorar, este veredicto vira ✅.",
        "tr": "Şu an almaya değer Print yok: tüm kombinasyonların gerçek APY'si başabaşın (dolum risk maliyeti) altında, beklenen değer negatif. Koşullar düzelince bu karar ✅ olur.",
        "es": "Ningún Print vale la pena ahora: el APY real de todas las combinaciones está bajo el equilibrio (costo del riesgo de ejecución), valor esperado negativo. Cuando mejore, este veredicto pasa a ✅."},
    "pers_paid_hint": {
        "ko": "유료 모드는 본인 AI 키로 동작합니다. .env 파일에 "
              "ANTHROPIC_API_KEY=키 (또는 FREE_LLM_PROVIDER/FREE_LLM_KEY) "
              "를 넣고 봇을 다시 시작하면 그 AI가 대화로 답합니다.",
        "en": "Paid mode runs on your own AI key. Put ANTHROPIC_API_KEY="
              "yourkey (or FREE_LLM_PROVIDER/FREE_LLM_KEY) in .env and "
              "restart the bot; that AI then answers in conversation.",
        "zh": "付费模式使用您自己的 AI 密钥。在 .env 中填入 ANTHROPIC_API_KEY="
              "密钥（或 FREE_LLM_PROVIDER/FREE_LLM_KEY）并重启机器人即可。",
        "ja": "有料モードはご自身の AI キーで動きます。.env に ANTHROPIC_API_"
              "KEY=キー（または FREE_LLM_PROVIDER/FREE_LLM_KEY）を入れて"
              "ボットを再起動してください。",
        "vi": "Chế độ trả phí dùng khóa AI của bạn. Đặt ANTHROPIC_API_KEY="
              "khóa (hoặc FREE_LLM_PROVIDER/FREE_LLM_KEY) vào .env và khởi "
              "động lại bot.",
        "hi": "सशुल्क मोड आपकी अपनी AI कुंजी से चलता है। .env में ANTHROPIC_API_KEY="
              "कुंजी (या FREE_LLM_PROVIDER/FREE_LLM_KEY) डालें और बॉट पुनः आरंभ "
              "करें।",
        "id": "Mode berbayar memakai kunci AI Anda sendiri. Isi "
              "ANTHROPIC_API_KEY=kunci (atau FREE_LLM_PROVIDER/FREE_LLM_"
              "KEY) di .env lalu mulai ulang bot.",
        "ru": "Платный режим работает на вашем ключе ИИ. Впишите "
              "ANTHROPIC_API_KEY=ключ (или FREE_LLM_PROVIDER/FREE_LLM_KEY) "
              "в .env и перезапустите бота.",
        "pt": "O modo pago usa a sua própria chave de IA. Coloque "
              "ANTHROPIC_API_KEY=chave (ou FREE_LLM_PROVIDER/FREE_LLM_KEY) "
              "no .env e reinicie o bot.",
        "tr": "Ücretli mod kendi AI anahtarınızla çalışır. .env dosyasına "
              "ANTHROPIC_API_KEY=anahtar (veya FREE_LLM_PROVIDER/FREE_LLM_"
              "KEY) yazıp botu yeniden başlatın.",
        "es": "El modo de pago usa su propia clave de IA. Ponga "
              "ANTHROPIC_API_KEY=clave (o FREE_LLM_PROVIDER/FREE_LLM_KEY) "
              "en .env y reinicie el bot."},
    "chat_local_preparing": {
        "ko": "🔄 대화 모드를 준비하고 있습니다. 언어모델(약 1GB)을 처음 한 "
              "번만 내려받아요. 몇 분 뒤부터는 어떤 문장이든 자연스럽게 "
              "답해드립니다. 그동안은 기본 답변으로 응답합니다:",
        "en": "🔄 Preparing conversation mode: downloading the language "
              "model (about 1GB, one time). In a few minutes I will answer "
              "any sentence naturally. Until then, the standard answer:",
        "zh": "🔄 正在准备对话模式：首次下载语言模型（约1GB，仅一次）。几分钟"
              "后即可自然回答任何问题。在此期间先用基本回答：",
        "ja": "🔄 会話モードを準備中：言語モデル（約1GB）を初回のみダウンロード"
              "しています。数分後からどんな文章にも自然に答えます。それまでは"
              "基本回答で応答します：",
        "vi": "🔄 Đang chuẩn bị chế độ hội thoại: tải mô hình ngôn ngữ "
              "(khoảng 1GB, một lần). Vài phút nữa tôi sẽ trả lời tự nhiên "
              "mọi câu. Trong lúc chờ, câu trả lời cơ bản:",
        "hi": "🔄 वार्तालाप मोड तैयार हो रहा है: भाषा मॉडल (लगभग 1GB, एक बार) डाउनलोड "
              "हो रहा है। कुछ मिनटों में किसी भी वाक्य का जवाब दूँगा। तब तक बुनियादी "
              "जवाब:",
        "id": "🔄 Menyiapkan mode percakapan: mengunduh model bahasa "
              "(sekitar 1GB, sekali saja). Beberapa menit lagi saya bisa "
              "menjawab kalimat apa pun. Sementara itu, jawaban dasar:",
        "ru": "🔄 Готовлю режим беседы: скачиваю языковую модель (около "
              "1ГБ, один раз). Через несколько минут отвечу на любую фразу. "
              "А пока стандартный ответ:",
        "pt": "🔄 Preparando o modo de conversa: baixando o modelo de "
              "linguagem (cerca de 1GB, uma vez). Em alguns minutos "
              "responderei qualquer frase. Até lá, a resposta padrão:",
        "tr": "🔄 Sohbet modu hazırlanıyor: dil modeli indiriliyor (yaklaşık "
              "1GB, bir kez). Birkaç dakika içinde her cümleye doğal yanıt "
              "veririm. O zamana dek standart yanıt:",
        "es": "🔄 Preparando el modo conversación: descargando el modelo de "
              "lenguaje (aprox. 1GB, una vez). En unos minutos responderé "
              "cualquier frase. Mientras tanto, la respuesta estándar:"},
    "exec_no_stop_platform": {
        "ko": "이 컴퓨터에서 실행 중인 프로그램 목록을 읽을 수 없어 껐는지 "
              "확인하지 못했습니다. 잠시 뒤 다시 꺼달라고 말해 주세요. "
              "그래도 안 되면 컴퓨터를 재시작하면 확실히 꺼집니다.",
        "en": "Could not read this computer's running programs, so I cannot "
              "confirm it stopped. Ask me to stop it again in a moment. If "
              "that fails, restarting the computer always works.",
        "zh": "无法读取本机运行中的程序，因此无法确认是否已停止。请稍后再让我"
              "停止一次。若仍无效，重启电脑一定可以。",
        "ja": "このパソコンの実行中プログラムを読めず、停止を確認できません"
              "でした。少し経ってからもう一度停止と伝えてください。それでも"
              "だめならパソコンの再起動で確実に止まります。",
        "vi": "Không đọc được danh sách chương trình đang chạy nên chưa xác "
              "nhận được đã dừng. Lát nữa hãy bảo tôi dừng lại. Nếu vẫn "
              "không được, khởi động lại máy tính chắc chắn dừng.",
        "hi": "इस कंप्यूटर के चल रहे प्रोग्राम नहीं पढ़ पाया, इसलिए बंद होना पक्का नहीं। "
              "थोड़ी देर बाद फिर बंद करने को कहें। तब भी न हो तो रीस्टार्ट करें।",
        "id": "Tidak bisa membaca daftar program yang berjalan, jadi belum "
              "bisa memastikan sudah berhenti. Minta saya hentikan lagi "
              "sebentar lagi. Kalau gagal, restart komputer pasti berhasil.",
        "ru": "Не удалось прочитать список запущенных программ, поэтому "
              "остановка не подтверждена. Попросите остановить ещё раз чуть "
              "позже. Если не поможет, перезагрузите компьютер.",
        "pt": "Não consegui ler os programas em execução, então não confirmo "
              "a parada. Peça para eu parar de novo daqui a pouco. Se "
              "falhar, reiniciar o computador resolve.",
        "tr": "Bu bilgisayarda çalışan programları okuyamadım, bu yüzden "
              "durduğunu doğrulayamıyorum. Biraz sonra tekrar durdurmamı "
              "isteyin. Olmazsa bilgisayarı yeniden başlatın.",
        "es": "No pude leer los programas en ejecución, así que no confirmo "
              "la parada. Pídame detenerlo otra vez en un momento. Si falla, "
              "reiniciar el ordenador siempre funciona."},
    "gloss_none": {
        "ko": "그 용어는 아직 사전에 없습니다. /menu 로 물어볼 수 있는 것들을 "
              "보여드립니다.",
        "en": "That term is not in my glossary yet. /menu shows what you "
              "can ask.",
        "zh": "该术语还不在词典里。/menu 查看可问内容。",
        "ja": "その用語はまだ辞書にありません。/menu をご覧ください。",
        "vi": "Thuật ngữ chưa có trong từ điển. Xem /menu.",
        "hi": "यह शब्द अभी शब्दकोश में नहीं। /menu देखें।",
        "id": "Istilah belum ada di kamus. Lihat /menu.",
        "ru": "Термина пока нет в словаре. См. /menu.",
        "pt": "Termo ainda não está no glossário. Veja /menu.",
        "tr": "Terim sözlükte yok. /menu.",
        "es": "Término aún no está en el glosario. Vea /menu."},
}

# Mini glossary for the free tier ("펀딩이 뭐야?"). Term -> concept key.
# Native spellings for every menu language, so "资金费是什么" works too.
GLOSS = {
    "펀딩": ("funding",), "funding": ("funding",), "资金费": ("funding",),
    "ファンディング": ("funding",), "фандинг": ("funding",),
    "фондирован": ("funding",), "financiamento": ("funding",),
    "फंडिंग": ("funding",),
    "캐리": ("carry",), "carry": ("carry",), "套利": ("carry",),
    "キャリー": ("carry",), "кэрри": ("carry",),
    "브래킷": ("bracket",), "bracket": ("bracket",),
    "ブラケット": ("bracket",), "брекет": ("bracket",),
    "픽": ("pick",), "pick": ("pick",), "봉인": ("pick",),
    "ピック": ("pick",), "пик": ("pick",),
    "레버리지": ("leverage",), "leverage": ("leverage",),
    "杠杆": ("leverage",), "レバレッジ": ("leverage",),
    "плечо": ("leverage",), "kaldıraç": ("leverage",),
    "alavancagem": ("leverage",), "apalancamiento": ("leverage",),
    "đòn bẩy": ("leverage",), "लीवरेज": ("leverage",),
    "청산": ("liquidation",), "liquidation": ("liquidation",),
    "爆仓": ("liquidation",), "清算": ("liquidation",),
    "ликвидаци": ("liquidation",), "liquidação": ("liquidation",),
    "liquidación": ("liquidation",), "likidasyon": ("liquidation",),
    "likuidasi": ("liquidation",), "thanh lý": ("liquidation",),
    "숏": ("short",), "short": ("short",), "做空": ("short",),
    "ショート": ("short",), "шорт": ("short",),
    "롱": ("long",), "long": ("long",), "做多": ("long",),
    "ロング": ("long",), "лонг": ("long",),
}

GLOSS_TEXT = {
    "funding": {
        "ko": "펀딩비: 무기한 선물에서 롱과 숏이 주기적으로 주고받는 수수료입니다. "
              "선물 가격이 현물보다 높으면 롱이 숏에게, 낮으면 숏이 롱에게 냅니다. "
              "/funding 으로 지금 순위를 볼 수 있습니다.",
        "en": "Funding: the periodic fee longs and shorts exchange on "
              "perpetuals. Perp above spot: longs pay shorts; below: shorts "
              "pay longs. /funding shows the current ranking.",
        "zh": "资金费：永续合约中多空双方定期互付的费用。合约价高于现货时多头付"
              "空头，反之空头付多头。/funding 查看当前排行。",
        "ja": "ファンディング：無期限先物でロングとショートが定期的にやり取り"
              "する手数料。先物が現物より高いとロングが支払い、低いとショート"
              "が支払う。/funding で現在のランキング。",
        "vi": "Funding: phí mà long và short trao đổi định kỳ trên hợp đồng "
              "vĩnh viễn. Perp cao hơn spot: long trả; thấp hơn: short trả. "
              "/funding xem xếp hạng.",
        "hi": "फंडिंग: परपेचुअल में लॉन्ग-शॉर्ट के बीच नियमित फीस। पर्प स्पॉट से ऊपर: लॉन्ग "
              "देता है; नीचे: शॉर्ट देता है। /funding देखें।",
        "id": "Funding: biaya berkala antara long dan short di perpetual. "
              "Perp di atas spot: long membayar; di bawah: short membayar. "
              "/funding untuk peringkat.",
        "ru": "Фандинг: периодическая плата между лонгами и шортами на "
              "перпетуалах. Перп выше спота — платят лонги, ниже — шорты. "
              "/funding — рейтинг.",
        "pt": "Funding: taxa periódica entre longs e shorts nos perpétuos. "
              "Perp acima do spot: longs pagam; abaixo: shorts pagam. "
              "/funding mostra o ranking.",
        "tr": "Funding: perpetual'da long ve short arasında dönemsel ücret. "
              "Perp spotun üstünde: long öder; altında: short öder. "
              "/funding sıralamayı gösterir.",
        "es": "Funding: tarifa periódica entre largos y cortos en "
              "perpetuos. Perp sobre spot: pagan los largos; debajo: los "
              "cortos. /funding muestra el ranking."},
    "carry": {
        "ko": "펀딩 캐리: 현물(또는 반대 포지션)과 무기한을 동시에 들어 가격 "
              "위험을 지우고 펀딩비만 수취하는 전략입니다. /carry 로 지금 "
              "자리를 볼 수 있습니다.",
        "en": "Funding carry: hold the perp against an offsetting leg so "
              "price risk cancels and you collect funding. /carry shows "
              "current seats.",
        "zh": "资金费套利：同时持有对冲两腿，抵消价格风险，只收取资金费。"
              "/carry 查看当前机会。",
        "ja": "ファンディングキャリー：反対の建玉を同時に持ち価格リスクを消して"
              "ファンディングだけ受け取る戦略。/carry で現在の候補。",
        "vi": "Funding carry: giữ hai chân đối nghịch để khử rủi ro giá và "
              "chỉ nhận funding. /carry.",
        "hi": "फंडिंग कैरी: विपरीत पोजीशन साथ रखें, कीमत जोखिम कटे, सिर्फ फंडिंग लें। "
              "/carry।",
        "id": "Funding carry: pegang dua kaki berlawanan agar risiko harga "
              "hilang dan hanya menerima funding. /carry.",
        "ru": "Кэрри: держите перп против встречной ноги — ценовой риск "
              "гасится, остаётся фандинг. /carry.",
        "pt": "Carry de funding: mantenha pernas opostas, o risco de preço "
              "se anula e você coleta o funding. /carry.",
        "tr": "Funding carry: karşıt bacaklarla fiyat riskini sıfırlayıp "
              "sadece funding toplarsınız. /carry.",
        "es": "Carry de funding: mantenga piernas opuestas, el riesgo de "
              "precio se anula y cobra el funding. /carry."},
    "bracket": {
        "ko": "브래킷: 진입과 동시에 거래소에 익절(예상변동 1.5배)과 손절(1.0배) "
              "주문을 함께 걸어두는 방식입니다. 봇이 죽어도 거래소가 지킵니다.",
        "en": "Bracket: entry placed together with exchange-side TP (1.5x "
              "expected move) and SL (1.0x). The exchange protects the "
              "position even if the bot dies.",
        "zh": "Bracket：下单同时在交易所挂好止盈（预期波动1.5倍）和止损"
              "（1.0倍）。即使机器人挂了，交易所也会守住。",
        "ja": "ブラケット：エントリーと同時に取引所へ TP（予想変動1.5倍）と "
              "SL（1.0倍）を置く方式。ボットが落ちても取引所が守る。",
        "vi": "Bracket: vào lệnh kèm TP (1,5x biến động dự kiến) và SL "
              "(1,0x) ngay trên sàn. Bot chết thì sàn vẫn bảo vệ.",
        "hi": "ब्रैकेट: एंट्री के साथ एक्सचेंज पर TP (1.5x) और SL (1.0x) लगते हैं। बॉट "
              "बंद भी हो तो एक्सचेंज रक्षा करता है।",
        "id": "Bracket: entry dipasang bersama TP (1,5x pergerakan harapan) "
              "dan SL (1,0x) di bursa. Bot mati pun bursa menjaga.",
        "ru": "Брекет: вход сразу с биржевыми TP (1,5x ожидаемого хода) и "
              "SL (1,0x). Даже если бот упал, биржа защищает.",
        "pt": "Bracket: entrada junto com TP (1,5x do movimento esperado) e "
              "SL (1,0x) na corretora. Mesmo se o bot cair, a corretora "
              "protege.",
        "tr": "Bracket: girişle birlikte borsaya TP (beklenen hareketin 1,5 "
              "katı) ve SL (1,0 kat) konur. Bot çökse de borsa korur.",
        "es": "Bracket: la entrada va junto con TP (1,5x del movimiento "
              "esperado) y SL (1,0x) en el exchange. Aunque el bot caiga, "
              "el exchange protege."},
    "pick": {
        "ko": "픽(봉인): 매 시각 전 종목의 예상 변동폭을 재서 큰 순서로 뽑은 "
              "추천 목록입니다. 결과가 나오기 전에 파일로 봉인해 두고 나중에 "
              "채점합니다. /pick 으로 최신 픽을 봅니다.",
        "en": "Pick (sealed): every hour all symbols are ranked by expected "
              "move and the list is sealed to a file before outcomes exist, "
              "then graded later. /pick shows the latest.",
        "zh": "Pick（封存）：每小时按预期波动幅度给全部币种排序，在结果出现前"
              "把名单封存成文件，事后评分。/pick 查看最新。",
        "ja": "ピック（封印）：毎時、全銘柄を予想変動幅で順位付けし、結果が出る"
              "前にファイルへ封印して後で採点します。/pick で最新。",
        "vi": "Pick (niêm phong): mỗi giờ xếp hạng mọi mã theo biến động dự "
              "kiến, niêm phong trước khi có kết quả rồi chấm sau. /pick.",
        "hi": "पिक (सीलबंद): हर घंटे सभी सिंबल अपेक्षित चाल से रैंक होते हैं, नतीजे से पहले "
              "सील, बाद में ग्रेड। /pick।",
        "id": "Pick (tersegel): tiap jam semua simbol diperingkat menurut "
              "pergerakan harapan, disegel sebelum hasil ada, dinilai "
              "belakangan. /pick.",
        "ru": "Пик (запечатан): каждый час все символы ранжируются по "
              "ожидаемому ходу, список запечатывается до результата и "
              "оценивается потом. /pick.",
        "pt": "Pick (selado): a cada hora todos os símbolos são ranqueados "
              "pelo movimento esperado, selados antes do resultado e "
              "avaliados depois. /pick.",
        "tr": "Pick (mühürlü): her saat tüm semboller beklenen harekete "
              "göre sıralanır, sonuç çıkmadan mühürlenir, sonra notlanır. "
              "/pick.",
        "es": "Pick (sellado): cada hora se ranquean todos los símbolos por "
              "movimiento esperado, se sella antes del resultado y se "
              "califica después. /pick."},
    "leverage": {
        "ko": "레버리지: 증거금의 몇 배 크기로 포지션을 여는지입니다. 5배면 "
              "가격이 1% 움직일 때 증거금 기준 5% 움직입니다. 청산 위험도 "
              "같이 커집니다.",
        "en": "Leverage: position size as a multiple of margin. At 5x a 1% "
              "price move is 5% on margin. Liquidation risk grows with it.",
        "zh": "杠杆：仓位是保证金的几倍。5倍时价格动1%，保证金就动5%。爆仓"
              "风险同样放大。",
        "ja": "レバレッジ：証拠金の何倍で建てるか。5倍なら価格1%の動きが証拠金"
              "基準5%。清算リスクも同じだけ増える。",
        "vi": "Đòn bẩy: vị thế gấp mấy lần ký quỹ. 5x thì giá 1% = 5% trên "
              "ký quỹ. Rủi ro thanh lý tăng theo.",
        "hi": "लीवरेज: मार्जिन का कितना गुना। 5x पर 1% चाल = मार्जिन पर 5%। "
              "लिक्विडेशन जोखिम भी बढ़ता है।",
        "id": "Leverage: posisi berapa kali margin. 5x: harga 1% = 5% pada "
              "margin. Risiko likuidasi ikut naik.",
        "ru": "Плечо: размер позиции в кратных к марже. При 5x ход цены 1% "
              "— это 5% на маржу. Риск ликвидации растёт так же.",
        "pt": "Alavancagem: posição como múltiplo da margem. Em 5x, 1% no "
              "preço = 5% na margem. O risco de liquidação cresce junto.",
        "tr": "Kaldıraç: pozisyonun teminatın kaç katı olduğu. 5x'te %1 "
              "hareket, teminatta %5'tir. Likidasyon riski de büyür.",
        "es": "Apalancamiento: posición como múltiplo del margen. A 5x, 1% "
              "del precio = 5% del margen. El riesgo de liquidación crece "
              "igual."},
    "liquidation": {
        "ko": "청산: 손실이 증거금을 다 먹으면 거래소가 강제로 포지션을 닫는 "
              "것입니다. 브래킷의 손절은 그 전에 먼저 끊어서 청산을 피하기 "
              "위한 장치입니다.",
        "en": "Liquidation: the exchange force-closes a position when losses "
              "eat the margin. The bracket stop exists to cut earlier and "
              "avoid it.",
        "zh": "爆仓：亏损吃光保证金时交易所强制平仓。Bracket 的止损就是为了"
              "在此之前先砍单、避免爆仓。",
        "ja": "清算：損失が証拠金を食い尽くすと取引所が強制決済。ブラケットの "
              "SL はその前に切って清算を避けるための仕組み。",
        "vi": "Thanh lý: lỗ ăn hết ký quỹ thì sàn đóng lệnh cưỡng bức. SL "
              "của bracket cắt sớm hơn để tránh điều đó.",
        "hi": "लिक्विडेशन: घाटा मार्जिन खा ले तो एक्सचेंज पोजीशन बंद कर देता है। ब्रैकेट "
              "का SL पहले काटकर इससे बचाता है।",
        "id": "Likuidasi: bila rugi menghabiskan margin, bursa menutup "
              "paksa. SL bracket memotong lebih dulu untuk menghindarinya.",
        "ru": "Ликвидация: когда убыток съедает маржу, биржа принудительно "
              "закрывает позицию. Стоп брекета режет раньше, чтобы этого "
              "избежать.",
        "pt": "Liquidação: quando a perda consome a margem, a corretora "
              "fecha à força. O SL do bracket corta antes para evitá-la.",
        "tr": "Likidasyon: zarar teminatı bitirince borsa pozisyonu zorla "
              "kapatır. Bracket SL bundan önce keser.",
        "es": "Liquidación: cuando la pérdida consume el margen, el "
              "exchange cierra a la fuerza. El SL del bracket corta antes "
              "para evitarla."},
    "short": {
        "ko": "숏: 내리면 버는 포지션입니다. 빌려서 팔고 싸게 되사는 구조라 "
              "가격이 오르면 손실입니다.",
        "en": "Short: a position that profits when price falls and loses "
              "when it rises.",
        "zh": "做空：价格下跌时盈利、上涨时亏损的仓位。",
        "ja": "ショート：下がれば利益、上がれば損失のポジション。",
        "vi": "Short: lãi khi giá giảm, lỗ khi giá tăng.",
        "hi": "शॉर्ट: गिरने पर लाभ, चढ़ने पर हानि।",
        "id": "Short: untung saat harga turun, rugi saat naik.",
        "ru": "Шорт: прибыль при падении цены, убыток при росте.",
        "pt": "Short: lucra na queda, perde na alta.",
        "tr": "Short: fiyat düşünce kazanır, yükselince kaybeder.",
        "es": "Short: gana si el precio baja, pierde si sube."},
    "long": {
        "ko": "롱: 오르면 버는 포지션입니다.",
        "en": "Long: a position that profits when price rises.",
        "zh": "做多：价格上涨时盈利的仓位。",
        "ja": "ロング：上がれば利益のポジション。",
        "vi": "Long: lãi khi giá tăng.",
        "hi": "लॉन्ग: चढ़ने पर लाभ।",
        "id": "Long: untung saat harga naik.",
        "ru": "Лонг: прибыль при росте цены.",
        "pt": "Long: lucra na alta.",
        "tr": "Long: fiyat yükselince kazanır.",
        "es": "Long: gana si el precio sube."},
}


def gloss(term_key: str, lang: str) -> str:
    d = GLOSS_TEXT.get(term_key, {})
    return d.get(lang) or d.get("en") or ""

# Intent keywords, lowercase; matched as substrings of the lowercased text.
# English is always included as a base, the user's language is added on top.
INTENT = {
    "ko": {
        "pick": ["픽", "추천", "종목", "뭐 사", "뭐 잡", "뭐사", "뭐잡",
                 "오를", "유망"],
        "carry": ["캐리", "차익", "자리"],
        "funding": ["펀딩"],
        "balance": ["잔고", "잔액", "얼마 있", "돈", "계좌", "수익", "손실",
                    "익절", "손절"],
        "trades": ["체결", "이력", "내역", "거래"],
        "bot": ["봇", "상태", "잘 돌", "돌아가", "포지션", "매매", "지금 뭐",
                "뭐하고", "뭐 하고", "승률", "성적", "적중"],
        "why": ["왜", "이유", "먹혔", "잃었", "졌", "털렸"],
        "pick_stock": ["주식만", "주식 픽", "주식 추천", "주식"],
        "pick_coin": ["코인만", "코인 픽", "코인 추천"],
        "greet": ["안녕", "하이", "ㅎㅇ", "반가", "안뇽", "헬로"],
        "thanks": ["고마", "감사", "땡큐", "굿", "잘했", "좋아", "멋지"],
        "alerts": ["경고", "알림", "알람"],
        "help": ["도움", "도와", "사용법", "쓰는 법", "뭐 할 수", "뭐할수",
                 "기능", "할 줄"],
        "auto_on": ["자동매매", "자동 매매", "매매 시작", "매매 켜",
                    "봇 켜", "봇 시작", "트레이딩 시작", "돌려줘", "가동"],
        "auto_off": ["매매 꺼", "매매 중지", "매매 멈춰", "봇 꺼", "봇 중지",
                     "봇 멈춰", "매매 그만", "정지시켜", "중단"],
        "yes": ["예", "네", "응", "ㅇㅇ", "그래", "좋아 해", "확인", "고고",
                "시작해", "해줘"],
        "no": ["아니", "취소", "안 해", "안해", "하지마", "하지 마", "노노"],
        "whatis": ["뭐야", "뭔데", "무엇", "뜻이", "란?", "이란", "라는게",
                   "라는 게", "개념"],
        "update": ["업데이트", "판올림", "최신 버전으로", "최신버전으로"],
        "print_q": ["프린트", "print"]},
    "en": {
        "pick": ["pick", "recommend", "what to buy", "what should i buy",
                 "suggestion", "ranking"],
        "carry": ["carry", "arbitrage", " arb "],
        "funding": ["funding"],
        "balance": ["balance", "how much", "money", "account", "profit",
                    "pnl", "loss"],
        "trades": ["trade", "fill", "history"],
        "bot": ["bot", "status", "running", "position", "what are you doing",
                "win rate", "performance"],
        "why": ["why", "reason", "lost", "won"],
        "pick_stock": ["stocks only", "stock pick", "equities", "stocks"],
        "pick_coin": ["coins only", "coin pick", "crypto only"],
        "greet": ["hello", "hi ", "hey", "good morning", "good evening"],
        "thanks": ["thank", "thx", "nice", "great", "good job", "well done"],
        "alerts": ["alert", "warning", "notification", "ping"],
        "help": ["help", "how to", "what can you", "usage", "guide"],
        "auto_on": ["auto trade", "auto-trade", "start trading", "start bot",
                    "turn on", "run the bot"],
        "auto_off": ["stop trading", "stop bot", "turn off", "halt",
                     "shut down"],
        "yes": ["yes", "yeah", "yep", "ok", "okay", "confirm", "go ahead",
                "do it"],
        "no": ["no ", "nope", "cancel", "don't", "stop it", "never mind"],
        "whatis": ["what is", "what's", "meaning of", "explain", "define"],
        "update": ["update", "upgrade"],
        "print_q": ["print"]},
    "zh": {
        "pick": ["推荐", "买什么", "选币", "排行"],
        "carry": ["套利", "费率差"],
        "funding": ["资金费"],
        "balance": ["余额", "多少钱", "账户"],
        "trades": ["成交", "记录", "交易"],
        "bot": ["机器人", "状态", "运行"],
        "why": ["为什么", "为何", "原因", "亏", "赚了"]},
    "ja": {
        "pick": ["おすすめ", "推奨", "銘柄", "何を買", "ピック"],
        "carry": ["キャリー", "さや取り", "アービトラージ"],
        "funding": ["ファンディング"],
        "balance": ["残高", "いくら", "口座"],
        "trades": ["約定", "履歴", "取引"],
        "bot": ["ボット", "状態", "動いて"],
        "why": ["なぜ", "理由", "負け", "勝った", "損した"]},
    "vi": {
        "pick": ["gợi ý", "đề xuất", "mua gì", "chọn coin"],
        "carry": ["chênh lệch"],
        "funding": ["phí funding"],
        "balance": ["số dư", "bao nhiêu tiền", "tài khoản"],
        "trades": ["khớp lệnh", "lịch sử", "giao dịch"],
        "bot": ["trạng thái", "đang chạy"],
        "why": ["tại sao", "vì sao", "lý do", "thua", "thắng"]},
    "hi": {
        "pick": ["पिक", "सिफारिश", "क्या खरीद", "सुझाव"],
        "carry": ["कैरी", "आर्बिट्राज"],
        "funding": ["फंडिंग"],
        "balance": ["बैलेंस", "कितना पैसा", "खाता", "शेष"],
        "trades": ["ट्रेड", "इतिहास", "सौदे"],
        "bot": ["बॉट", "स्थिति", "चल रहा"],
        "why": ["क्यों", "कारण", "हार", "जीत"]},
    "id": {
        "pick": ["rekomendasi", "pilihan", "beli apa", "saran"],
        "carry": ["arbitrase", "selisih"],
        "funding": ["pendanaan"],
        "balance": ["saldo", "berapa uang", "akun"],
        "trades": ["transaksi", "riwayat", "eksekusi"],
        "bot": ["berjalan"],
        "why": ["kenapa", "mengapa", "alasan", "kalah", "menang"]},
    "ru": {
        "pick": ["рекомендац", "пик", "что купить", "совет"],
        "carry": ["керри", "арбитраж"],
        "funding": ["фандинг", "ставка финансирования"],
        "balance": ["баланс", "сколько денег", "счёт", "счет"],
        "trades": ["сделк", "истори", "исполнен"],
        "bot": ["бот", "статус", "работает"],
        "why": ["почему", "зачем", "причин", "проиграл", "выиграл"]},
    "pt": {
        "pick": ["recomenda", "escolha", "o que comprar", "sugest"],
        "carry": ["arbitragem"],
        "funding": ["financiamento"],
        "balance": ["saldo", "quanto dinheiro", "conta"],
        "trades": ["operaç", "negocia", "histórico", "historico", "execuç"],
        "bot": ["rodando"],
        "why": ["por que", "porque", "motivo", "perdeu", "ganhou"]},
    "tr": {
        "pick": ["öneri", "tavsiye", "ne alayım", "seçim"],
        "carry": ["arbitraj"],
        "funding": ["fonlama"],
        "balance": ["bakiye", "ne kadar param", "hesap"],
        "trades": ["işlem", "geçmiş", "gecmis"],
        "bot": ["durum", "çalışıyor"],
        "why": ["neden", "niçin", "sebep", "kaybet", "kazand"]},
    "es": {
        "pick": ["recomendac", "elección", "eleccion", "qué comprar",
                 "que comprar", "sugerencia"],
        "carry": ["arbitraje"],
        "funding": ["financiación", "financiacion", "fondeo"],
        "balance": ["saldo", "cuánto dinero", "cuanto dinero", "cuenta"],
        "trades": ["operac", "historial", "ejecuc"],
        "bot": ["estado", "funcionando"],
        "why": ["por qué", "por que", "motivo", "perdí", "perdi", "ganó"]},
}

# Coin/stock names per language -> Pacifica symbol. Lowercase; matched as
# substrings of the lowercased text. Only unambiguous names.
ALIAS_I18N = {
    "en": {"bitcoin": "BTC", "ethereum": "ETH", "ether": "ETH",
           "solana": "SOL", "dogecoin": "DOGE", "tesla": "TSLA",
           "nvidia": "NVDA", "google": "GOOGL", "micron": "MU",
           "sandisk": "SNDK", "samsung": "SAMSUNG", "hynix": "SKHYNIX"},
    "zh": {"比特币": "BTC", "以太坊": "ETH", "以太币": "ETH",
           "索拉纳": "SOL", "狗狗币": "DOGE", "特斯拉": "TSLA",
           "英伟达": "NVDA", "谷歌": "GOOGL", "美光": "MU",
           "三星": "SAMSUNG", "海力士": "SKHYNIX"},
    "ja": {"ビットコイン": "BTC", "ビット": "BTC", "イーサリアム": "ETH",
           "イーサ": "ETH", "ソラナ": "SOL", "ドージ": "DOGE",
           "テスラ": "TSLA", "エヌビディア": "NVDA", "グーグル": "GOOGL",
           "マイクロン": "MU", "サムスン": "SAMSUNG"},
    "ru": {"биткоин": "BTC", "биток": "BTC", "эфириум": "ETH",
           "эфир": "ETH", "солана": "SOL", "тесла": "TSLA",
           "нвидиа": "NVDA", "гугл": "GOOGL"},
    "hi": {"बिटकॉइन": "BTC", "इथेरियम": "ETH", "सोलाना": "SOL",
           "डॉजकॉइन": "DOGE", "टेस्ला": "TSLA"},
    # vi/id/pt/tr/es commonly write the latin names covered by "en".
    "es": {"bitcóin": "BTC"},
}


def tr(key: str, lang: str) -> str:
    d = T.get(key, {})
    return d.get(lang) or d.get("en") or d.get("any") or ""


# Conversation intents for languages whose main INTENT block predates them.
# ko/en carry these inside INTENT itself; this table fills in the rest so
# every menu language can greet, thank, confirm and ask "what is X"
# natively. intent_words() unions both tables.
INTENT_EXTRA = {
    "zh": {"print_q": ["print", "打印"], "update": ["更新", "升级"], "greet": ["你好", "您好", "嗨"], "thanks": ["谢谢", "感谢", "辛苦"],
           "alerts": ["提醒", "警告", "通知"],
           "help": ["帮助", "怎么用", "能做什么", "功能"],
           "auto_on": ["自动交易", "开始交易", "启动机器人", "开机"],
           "auto_off": ["停止交易", "关闭机器人", "停机", "别交易"],
           "yes": ["是", "好", "确认", "开始吧", "可以"],
           "no": ["不", "取消", "算了", "别"],
           "whatis": ["是什么", "什么意思", "解释"]},
    "ja": {"print_q": ["プリント", "print"], "update": ["アップデート", "更新"], "greet": ["こんにちは", "こんばんは", "やあ"],
           "thanks": ["ありがとう", "感謝", "助かる"],
           "alerts": ["通知", "警告", "アラート"],
           "help": ["ヘルプ", "使い方", "何ができる", "機能"],
           "auto_on": ["自動売買", "取引開始", "ボット起動", "動かして"],
           "auto_off": ["売買停止", "取引停止", "ボット停止", "止めて"],
           "yes": ["はい", "うん", "確認", "やって"],
           "no": ["いいえ", "キャンセル", "やめて", "中止"],
           "whatis": ["とは", "何ですか", "って何", "意味"]},
    "vi": {"print_q": ["print"], "update": ["cập nhật"], "greet": ["xin chào", "chào"], "thanks": ["cảm ơn", "cám ơn"],
           "alerts": ["cảnh báo", "thông báo"],
           "help": ["trợ giúp", "cách dùng", "làm được gì"],
           "auto_on": ["giao dịch tự động", "bật bot", "chạy bot"],
           "auto_off": ["dừng giao dịch", "tắt bot", "ngừng bot"],
           "yes": ["vâng", "đồng ý", "ừ"], "no": ["không", "hủy", "thôi"],
           "whatis": ["là gì", "nghĩa là"]},
    "hi": {"print_q": ["प्रिंट", "print"], "update": ["अपडेट"], "greet": ["नमस्ते", "हैलो"], "thanks": ["धन्यवाद", "शुक्रिया"],
           "alerts": ["चेतावनी", "सूचना"],
           "help": ["मदद", "कैसे", "क्या कर सकते"],
           "auto_on": ["ऑटो ट्रेडिंग", "बॉट चालू", "ट्रेडिंग शुरू"],
           "auto_off": ["ट्रेडिंग बंद", "बॉट बंद", "रोक दो"],
           "yes": ["हाँ", "हां", "ठीक"], "no": ["नहीं", "रद्द"],
           "whatis": ["क्या है", "मतलब"]},
    "id": {"print_q": ["print"], "update": ["perbarui", "pembaruan"], "greet": ["halo", "hai", "selamat"],
           "thanks": ["terima kasih", "makasih"],
           "alerts": ["peringatan", "notifikasi"],
           "help": ["bantuan", "cara pakai", "bisa apa"],
           "auto_on": ["trading otomatis", "nyalakan bot", "mulai trading"],
           "auto_off": ["hentikan trading", "matikan bot", "stop bot"],
           "yes": ["iya", "oke", "lanjut"], "no": ["tidak", "batal",
                                                   "jangan"],
           "whatis": ["apa itu", "artinya"]},
    "ru": {"print_q": ["принт", "print"], "update": ["обнови", "обновление"], "greet": ["привет", "здравствуй", "добрый"],
           "thanks": ["спасибо", "благодарю"],
           "alerts": ["предупреждени", "уведомлени"],
           "help": ["помощь", "как пользоваться", "что умеешь"],
           "auto_on": ["автоторговл", "запусти бота", "начни торговать",
                       "включи"],
           "auto_off": ["останови торговл", "выключи бота", "стоп бот"],
           "yes": ["да", "давай", "подтверждаю"],
           "no": ["нет", "отмена", "не надо"],
           "whatis": ["что такое", "что значит"]},
    "pt": {"print_q": ["print"], "update": ["atualiza", "atualização"], "greet": ["olá", "oi", "bom dia", "boa tarde"],
           "thanks": ["obrigado", "obrigada", "valeu"],
           "alerts": ["aviso", "alerta", "notificaç"],
           "help": ["ajuda", "como usar", "o que você faz"],
           "auto_on": ["trading automático", "ligar o bot",
                       "começar a operar", "iniciar bot"],
           "auto_off": ["parar de operar", "desligar o bot", "parar bot"],
           "yes": ["sim", "pode", "confirmo"],
           "no": ["não", "cancela", "deixa"],
           "whatis": ["o que é", "significa"]},
    "tr": {"print_q": ["print"], "update": ["güncelle"], "greet": ["merhaba", "selam"], "thanks": ["teşekkür", "sağol"],
           "alerts": ["uyarı", "bildirim"],
           "help": ["yardım", "nasıl kullan", "ne yapabilir"],
           "auto_on": ["otomatik işlem", "botu başlat", "işleme başla"],
           "auto_off": ["işlemi durdur", "botu kapat", "botu durdur"],
           "yes": ["evet", "olur", "onayl", "tamam"],
           "no": ["hayır", "iptal", "yapma"],
           "whatis": ["nedir", "ne demek"]},
    "es": {"print_q": ["print"], "update": ["actualiza", "actualización"], "greet": ["hola", "buenas"], "thanks": ["gracias"],
           "alerts": ["aviso", "alerta", "notificaci"],
           "help": ["ayuda", "cómo usar", "qué puedes"],
           "auto_on": ["trading automático", "enciende el bot",
                       "empezar a operar", "iniciar bot"],
           "auto_off": ["parar de operar", "apaga el bot", "detener bot"],
           "yes": ["sí", "dale", "confirmo", "vale"],
           "no": ["no ", "cancela", "olvida"],
           "whatis": ["qué es", "significa"]},
}


def intent_words(intent: str, lang: str):
    """Keyword list for one intent: English base plus the user's language."""
    out = list(INTENT.get("en", {}).get(intent, ()))
    if lang and lang != "en":
        out += INTENT.get(lang, {}).get(intent, ())
        out += INTENT_EXTRA.get(lang, {}).get(intent, ())
    return out


def alias_map(lang: str):
    """Symbol aliases: English base plus the user's language."""
    m = dict(ALIAS_I18N.get("en", {}))
    if lang and lang != "en":
        m.update(ALIAS_I18N.get(lang, {}))
    return m


def lang_kb() -> str:
    rows, row = [], []
    for code, label in LANGS:
        row.append({"text": label, "callback_data": "lang:" + code})
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return json.dumps({"inline_keyboard": rows})


# ── central bot: install desk, not a free window (2026-08-25) ────────────
T["install_pitch"] = {
 "ko": "Ocean Agent는 당신의 컴퓨터에서 당신의 키로 도는 제품이라, 이 공용 봇에는 보여드릴 게 없습니다. 설치하면 이 봇과 똑같은 봇이 당신 전용으로 돌면서 픽, 프린트 알림, 자동매매까지 전부 제공합니다.\n\n설치 안내: https://oceanagent.fi\n코드 전체 공개: https://oceanagent.fi/github\n커뮤니티: https://oceanagent.fi/telegram",
 "en": "Ocean Agent runs on your machine with your keys, so there is nothing to demo on this shared bot. Install it and this exact bot runs privately for you: picks, Print alerts, auto trading, everything.\n\nSetup guide: https://oceanagent.fi\nFull source: https://oceanagent.fi/github\nCommunity: https://oceanagent.fi/telegram",
 "zh": "Ocean Agent 在您自己的电脑上用您自己的密钥运行，所以这个公共机器人没有可展示的内容。安装后，同样的机器人将专属为您运行：推荐、Print 提醒、自动交易，一应俱全。\n\n安装指南: https://oceanagent.fi\n完整源码: https://oceanagent.fi/github\n社区: https://oceanagent.fi/telegram",
 "ja": "Ocean Agent はあなたのPCであなたの鍵で動く製品のため、この共用ボットにお見せできるものはありません。インストールすれば同じボットがあなた専用に動きます。ピック、Print通知、自動売買まで全部。\n\nセットアップ: https://oceanagent.fi\nソース全公開: https://oceanagent.fi/github\nコミュニティ: https://oceanagent.fi/telegram",
 "vi": "Ocean Agent chạy trên máy của bạn với khóa của bạn, nên bot chung này không có gì để xem. Cài đặt và chính bot này sẽ chạy riêng cho bạn: pick, cảnh báo Print, giao dịch tự động, tất cả.\n\nHướng dẫn: https://oceanagent.fi\nMã nguồn: https://oceanagent.fi/github\nCộng đồng: https://oceanagent.fi/telegram",
 "hi": "Ocean Agent आपकी मशीन पर आपकी keys से चलता है, इसलिए इस साझा बॉट पर दिखाने को कुछ नहीं है। इंस्टॉल करें और यही बॉट आपके लिए निजी रूप से चलेगा: picks, Print अलर्ट, ऑटो ट्रेडिंग, सब कुछ।\n\nसेटअप: https://oceanagent.fi\nपूरा सोर्स: https://oceanagent.fi/github\nसमुदाय: https://oceanagent.fi/telegram",
 "id": "Ocean Agent berjalan di komputer Anda dengan kunci Anda, jadi tidak ada yang bisa dilihat di bot bersama ini. Instal, dan bot yang sama berjalan khusus untuk Anda: pick, notifikasi Print, auto trading, semuanya.\n\nPanduan: https://oceanagent.fi\nKode sumber: https://oceanagent.fi/github\nKomunitas: https://oceanagent.fi/telegram",
 "ru": "Ocean Agent работает на вашей машине с вашими ключами, поэтому на этом общем боте нечего показывать. Установите, и этот же бот будет работать лично для вас: пики, оповещения Print, автоторговля, всё.\n\nУстановка: https://oceanagent.fi\nИсходники: https://oceanagent.fi/github\nСообщество: https://oceanagent.fi/telegram",
 "pt": "O Ocean Agent roda na sua máquina com as suas chaves, então não há nada para mostrar neste bot compartilhado. Instale e este mesmo bot roda privadamente para você: picks, alertas de Print, auto trading, tudo.\n\nGuia: https://oceanagent.fi\nCódigo aberto: https://oceanagent.fi/github\nComunidade: https://oceanagent.fi/telegram",
 "tr": "Ocean Agent kendi makinenizde kendi anahtarlarınızla çalışır; bu ortak botta gösterilecek bir şey yok. Kurun, aynı bot size özel çalışsın: pickler, Print uyarıları, otomatik işlem, hepsi.\n\nKurulum: https://oceanagent.fi\nKaynak kod: https://oceanagent.fi/github\nTopluluk: https://oceanagent.fi/telegram",
 "es": "Ocean Agent corre en tu máquina con tus llaves, así que no hay nada que mostrar en este bot compartido. Instálalo y este mismo bot corre en privado para ti: picks, alertas de Print, auto trading, todo.\n\nGuía: https://oceanagent.fi\nCódigo abierto: https://oceanagent.fi/github\nComunidad: https://oceanagent.fi/telegram"}

# ── personal onboarding: wallet address, API key (2026-08-25) ────────────
T["ask_wallet"] = {
 "ko": "지갑 주소를 붙여넣어 주세요 (파시피카 로그인에 쓰는 솔라나 지갑의 공개 주소). 조회 전용이라 안전합니다. 나중에 하려면 '아니'라고 답하세요.",
 "en": "Paste your wallet address (the public Solana address you log into Pacifica with). Read only, so it is safe. Reply 'no' to do this later.",
 "zh": "请粘贴您的钱包地址（登录 Pacifica 用的 Solana 公开地址）。仅用于查询，很安全。想稍后设置请回复 'no'。",
 "ja": "ウォレットアドレスを貼り付けてください（Pacifica にログインする Solana の公開アドレス）。照会専用なので安全です。後で設定するなら 'no' と返信してください。",
 "vi": "Dán địa chỉ ví của bạn (địa chỉ Solana công khai dùng đăng nhập Pacifica). Chỉ để tra cứu nên an toàn. Trả lời 'no' để làm sau.",
 "hi": "अपना वॉलेट पता पेस्ट करें (Pacifica में लॉगिन वाला सार्वजनिक Solana पता)। केवल देखने के लिए, सुरक्षित है। बाद में करने के लिए 'no' लिखें।",
 "id": "Tempel alamat dompet Anda (alamat publik Solana untuk login Pacifica). Hanya untuk melihat data, aman. Balas 'no' untuk nanti.",
 "ru": "Вставьте адрес кошелька (публичный адрес Solana для входа в Pacifica). Только для просмотра, это безопасно. Ответьте 'no', чтобы отложить.",
 "pt": "Cole o endereço da sua carteira (o endereço público Solana usado para entrar na Pacifica). Somente leitura, é seguro. Responda 'no' para fazer depois.",
 "tr": "Cüzdan adresinizi yapıştırın (Pacifica girişinde kullanılan herkese açık Solana adresi). Salt okunur, güvenlidir. Sonraya bırakmak için 'no' yazın.",
 "es": "Pega la dirección de tu billetera (la dirección pública de Solana con la que entras a Pacifica). Solo lectura, es segura. Responde 'no' para hacerlo luego."}
T["ask_apikey"] = {
 "ko": "이제 파시피카 API 키를 붙여넣어 주세요. app.pacifica.fi/apikey 에서 발급합니다. 거래 전용 키라 출금은 불가능하고, 입력하신 메시지는 보안을 위해 바로 지워집니다. 나중에 하려면 '아니'.",
 "en": "Now paste your Pacifica API key, issued at app.pacifica.fi/apikey. It can only trade, never withdraw, and your message is deleted right away for safety. Reply 'no' to do this later.",
 "zh": "现在请粘贴您的 Pacifica API 密钥（在 app.pacifica.fi/apikey 领取）。该密钥只能交易，不能提款，您的消息会立即删除以保安全。稍后设置请回复 'no'。",
 "ja": "次に Pacifica API キーを貼り付けてください（app.pacifica.fi/apikey で発行）。取引専用で出金は不可、貼り付けたメッセージは安全のためすぐ削除されます。後にするなら 'no'。",
 "vi": "Bây giờ dán API key Pacifica của bạn (cấp tại app.pacifica.fi/apikey). Chỉ giao dịch được, không rút tiền được, và tin nhắn của bạn sẽ bị xóa ngay để an toàn. Trả lời 'no' để làm sau.",
 "hi": "अब अपनी Pacifica API key पेस्ट करें (app.pacifica.fi/apikey से)। यह केवल ट्रेड कर सकती है, निकासी नहीं, और सुरक्षा के लिए आपका संदेश तुरंत हटा दिया जाएगा। बाद के लिए 'no' लिखें।",
 "id": "Sekarang tempel API key Pacifica Anda (dari app.pacifica.fi/apikey). Hanya bisa trading, tidak bisa menarik dana, dan pesan Anda langsung dihapus demi keamanan. Balas 'no' untuk nanti.",
 "ru": "Теперь вставьте ваш API ключ Pacifica (выдаётся на app.pacifica.fi/apikey). Он может только торговать, не выводить средства; ваше сообщение сразу удаляется для безопасности. Ответьте 'no', чтобы отложить.",
 "pt": "Agora cole sua API key da Pacifica (emitida em app.pacifica.fi/apikey). Ela só negocia, nunca saca, e sua mensagem é apagada imediatamente por segurança. Responda 'no' para depois.",
 "tr": "Şimdi Pacifica API anahtarınızı yapıştırın (app.pacifica.fi/apikey adresinden). Yalnızca işlem yapabilir, para çekemez; mesajınız güvenlik için hemen silinir. Sonraya bırakmak için 'no'.",
 "es": "Ahora pega tu API key de Pacifica (emitida en app.pacifica.fi/apikey). Solo puede operar, nunca retirar, y tu mensaje se borra de inmediato por seguridad. Responde 'no' para luego."}
T["setup_saved_addr"] = {
 "ko": "지갑 주소 저장 완료 ✅ 이제 잔고와 포지션 조회가 됩니다.",
 "en": "Wallet address saved ✅ Balance and position lookups now work.",
 "zh": "钱包地址已保存 ✅ 现在可以查询余额和持仓了。",
 "ja": "ウォレットアドレスを保存しました ✅ 残高・ポジション照会が使えます。",
 "vi": "Đã lưu địa chỉ ví ✅ Giờ có thể xem số dư và vị thế.",
 "hi": "वॉलेट पता सहेजा गया ✅ अब बैलेंस और पोज़िशन देख सकते हैं।",
 "id": "Alamat dompet tersimpan ✅ Saldo dan posisi kini bisa dilihat.",
 "ru": "Адрес кошелька сохранён ✅ Теперь доступны баланс и позиции.",
 "pt": "Endereço da carteira salvo ✅ Saldo e posições já funcionam.",
 "tr": "Cüzdan adresi kaydedildi ✅ Bakiye ve pozisyon sorguları çalışıyor.",
 "es": "Dirección guardada ✅ Ya funcionan las consultas de saldo y posiciones."}
T["setup_done_key"] = {
 "ko": "API 키 저장 완료 ✅ 이제 주문, 프린트 실행, 자동매매까지 전부 됩니다.",
 "en": "API key saved ✅ Orders, Print execution and auto trading are all live now.",
 "zh": "API 密钥已保存 ✅ 下单、Print 执行和自动交易全部可用。",
 "ja": "API キーを保存しました ✅ 注文・Print 実行・自動売買がすべて使えます。",
 "vi": "Đã lưu API key ✅ Đặt lệnh, Print và giao dịch tự động đều sẵn sàng.",
 "hi": "API key सहेजी गई ✅ ऑर्डर, Print और ऑटो ट्रेडिंग सब चालू।",
 "id": "API key tersimpan ✅ Order, Print, dan auto trading semua aktif.",
 "ru": "API ключ сохранён ✅ Ордера, Print и автоторговля полностью доступны.",
 "pt": "API key salva ✅ Ordens, Print e auto trading já funcionam.",
 "tr": "API anahtarı kaydedildi ✅ Emir, Print ve otomatik işlem tamamen açık.",
 "es": "API key guardada ✅ Órdenes, Print y auto trading ya funcionan."}
T["ask_budget"] = {
 "ko": "마지막으로, 봇이 굴릴 총액을 정하세요 (달러 숫자만). 가용 잔액 ${} 기준 추천은 ${}입니다. 포지션 여러 개로 나눠 들어가며 실제로 잠기는 증거금은 5배 기준 약 ${}. 언제든 바꿀 수 있고, 나중에 하려면 '아니'.",
 "en": "Last step: how much should the bot work with (dollars, number only)? With ${} available, we suggest ${}. It is split across several positions; actual locked margin at 5x is about ${}. You can change it anytime; reply 'no' to decide later.",
 "zh": "最后一步：机器人使用多少资金（仅数字，美元）？可用余额 ${}，建议 ${}。资金会分散到多个仓位，5倍杠杆下实际占用保证金约 ${}。随时可改；回复 'no' 稍后再定。",
 "ja": "最後に、ボットが運用する総額を決めてください（数字のみ、ドル）。利用可能 ${} に対し、おすすめは ${} です。複数ポジションに分散され、5倍で実際に拘束される証拠金は約 ${}。いつでも変更可、後にするなら 'no'。",
 "vi": "Bước cuối: bot nên dùng bao nhiêu (chỉ số, USD)? Với ${} khả dụng, gợi ý ${}. Chia thành nhiều vị thế; ký quỹ thực khóa ở 5x khoảng ${}. Đổi lúc nào cũng được; trả lời 'no' để quyết sau.",
 "hi": "अंतिम चरण: बॉट कितनी राशि से काम करे (केवल संख्या, डॉलर)? ${} उपलब्ध के साथ सुझाव ${} है। यह कई पोज़िशनों में बंटती है; 5x पर वास्तविक मार्जिन लगभग ${}। कभी भी बदलें; बाद के लिए 'no' लिखें।",
 "id": "Langkah terakhir: berapa dana yang dipakai bot (angka saja, dolar)? Dengan ${} tersedia, saran kami ${}. Dibagi ke beberapa posisi; margin terkunci pada 5x sekitar ${}. Bisa diubah kapan saja; balas 'no' untuk nanti.",
 "ru": "Последний шаг: с какой суммой работать боту (только число, в долларах)? Доступно ${}, рекомендуем ${}. Сумма делится на несколько позиций; фактическая маржа при 5x около ${}. Можно изменить в любой момент; 'no' — решить позже.",
 "pt": "Último passo: com quanto o bot deve operar (apenas número, dólares)? Com ${} disponível, sugerimos ${}. Divide se em várias posições; a margem travada em 5x é cerca de ${}. Mude quando quiser; responda 'no' para depois.",
 "tr": "Son adım: bot ne kadarla çalışsın (yalnız sayı, dolar)? Kullanılabilir ${} için önerimiz ${}. Birden çok pozisyona bölünür; 5x'te kilitlenen gerçek teminat yaklaşık ${}. İstediğinizde değiştirin; sonraya bırakmak için 'no'.",
 "es": "Último paso: ¿con cuánto debe operar el bot (solo número, dólares)? Con ${} disponible, sugerimos ${}. Se reparte en varias posiciones; el margen bloqueado a 5x es de unos ${}. Cámbialo cuando quieras; responde 'no' para luego."}
T["setup_bad_budget"] = {
 "ko": "숫자로만 답해주세요 (예: 150). 최소 $10. 나중에 하려면 '아니'.",
 "en": "Numbers only please (e.g. 150). Minimum $10. Reply 'no' to decide later.",
 "zh": "请只输入数字（如 150）。最低 $10。稍后再定请回复 'no'。",
 "ja": "数字のみで答えてください（例: 150）。最低 $10。後にするなら 'no'。",
 "vi": "Chỉ nhập số (vd: 150). Tối thiểu $10. 'no' để quyết sau.",
 "hi": "केवल संख्या लिखें (जैसे 150)। न्यूनतम $10। बाद के लिए 'no'।",
 "id": "Angka saja (mis. 150). Minimum $10. 'no' untuk nanti.",
 "ru": "Только число (напр. 150). Минимум $10. 'no' — позже.",
 "pt": "Apenas números (ex.: 150). Mínimo $10. 'no' para depois.",
 "tr": "Yalnız sayı yazın (örn. 150). En az $10. Sonrası için 'no'.",
 "es": "Solo números (p. ej. 150). Mínimo $10. 'no' para luego."}
T["setup_done_budget"] = {
 "ko": "거래 예산 저장 완료 ✅ ${}로 최대 {}개 포지션, 잠기는 증거금 약 ${}. '얼마로 바꿔' 한마디면 언제든 조정됩니다.",
 "en": "Trading budget saved ✅ ${} across up to {} positions, about ${} margin locked. Say a new amount anytime to change it.",
 "zh": "交易预算已保存 ✅ ${}，最多 {} 个仓位，占用保证金约 ${}。随时说个新数字即可更改。",
 "ja": "取引予算を保存しました ✅ ${} で最大 {} ポジション、拘束証拠金は約 ${}。新しい金額を言えばいつでも変更できます。",
 "vi": "Đã lưu ngân sách ✅ ${} cho tối đa {} vị thế, ký quỹ khoảng ${}. Nói số mới bất kỳ lúc nào để đổi.",
 "hi": "बजट सहेजा गया ✅ ${} से अधिकतम {} पोज़िशन, लगभग ${} मार्जिन। कभी भी नई राशि बताकर बदलें।",
 "id": "Anggaran tersimpan ✅ ${} untuk maks {} posisi, margin sekitar ${}. Sebut angka baru kapan saja untuk mengubah.",
 "ru": "Бюджет сохранён ✅ ${} на до {} позиций, маржа около ${}. Назовите новую сумму в любой момент.",
 "pt": "Orçamento salvo ✅ ${} em até {} posições, cerca de ${} de margem. Diga um novo valor a qualquer momento.",
 "tr": "Bütçe kaydedildi ✅ ${} ile en çok {} pozisyon, yaklaşık ${} teminat. Yeni bir tutar söyleyerek değiştirin.",
 "es": "Presupuesto guardado ✅ ${} en hasta {} posiciones, unos ${} de margen. Di un nuevo monto cuando quieras."}
T["setup_bad_addr"] = {
 "ko": "지갑 주소 형식이 아닙니다. 파시피카 로그인 지갑의 주소를 그대로 붙여넣어 주세요. 나중에 하려면 '아니'.",
 "en": "That does not look like a wallet address. Paste the address of the wallet you log into Pacifica with, or reply 'no' to skip for now.",
 "zh": "这不像钱包地址。请原样粘贴登录 Pacifica 的钱包地址，或回复 'no' 暂时跳过。",
 "ja": "ウォレットアドレスの形式ではありません。Pacifica にログインするウォレットのアドレスをそのまま貼り付けるか、'no' でスキップしてください。",
 "vi": "Không giống địa chỉ ví. Dán đúng địa chỉ ví đăng nhập Pacifica, hoặc 'no' để bỏ qua.",
 "hi": "यह वॉलेट पता नहीं लगता। Pacifica लॉगिन वॉलेट का पता वैसे ही पेस्ट करें, या 'no' लिखें।",
 "id": "Itu bukan alamat dompet. Tempel alamat dompet login Pacifica apa adanya, atau 'no' untuk lewati.",
 "ru": "Это не похоже на адрес кошелька. Вставьте адрес кошелька входа в Pacifica или ответьте 'no'.",
 "pt": "Isso não parece um endereço de carteira. Cole o endereço da carteira de login da Pacifica, ou 'no' para pular.",
 "tr": "Bu bir cüzdan adresine benzemiyor. Pacifica giriş cüzdanınızın adresini aynen yapıştırın veya 'no' yazın.",
 "es": "Eso no parece una dirección de billetera. Pega la dirección tal cual, o responde 'no' para omitir."}
T["setup_bad_key"] = {
 "ko": "API 키 형식이 아닙니다. app.pacifica.fi/apikey 에서 발급한 키를 그대로 붙여넣어 주세요. 나중에 하려면 '아니'.",
 "en": "That does not look like an API key. Paste the key issued at app.pacifica.fi/apikey exactly as given, or reply 'no' to skip for now.",
 "zh": "这不像 API 密钥。请原样粘贴 app.pacifica.fi/apikey 领取的密钥，或回复 'no' 跳过。",
 "ja": "API キーの形式ではありません。app.pacifica.fi/apikey で発行したキーをそのまま貼り付けるか、'no' でスキップしてください。",
 "vi": "Không giống API key. Dán đúng key từ app.pacifica.fi/apikey, hoặc 'no' để bỏ qua.",
 "hi": "यह API key नहीं लगती। app.pacifica.fi/apikey वाली key वैसे ही पेस्ट करें, या 'no' लिखें।",
 "id": "Itu bukan API key. Tempel key dari app.pacifica.fi/apikey apa adanya, atau 'no'.",
 "ru": "Это не похоже на API ключ. Вставьте ключ с app.pacifica.fi/apikey как есть или ответьте 'no'.",
 "pt": "Isso não parece uma API key. Cole a key de app.pacifica.fi/apikey exatamente como está, ou 'no' para pular.",
 "tr": "Bu bir API anahtarına benzemiyor. app.pacifica.fi/apikey anahtarını aynen yapıştırın veya 'no' yazın.",
 "es": "Eso no parece una API key. Pega la key de app.pacifica.fi/apikey tal cual, o responde 'no'."}

# ── tier selection, member Claude key, menu extras (2026-08-18) ──────────
T["tier_pick"] = {
 "en": "Choose your mode (switch anytime with /mode):\n🆓 Free mode: instant answers built from live data tables (picks, funding, balance, trades). Always free, nothing to set up.\n✨ Paid mode (Claude / ChatGPT / Gemini / Grok): what you type in Telegram goes straight to your chosen AI together with the live data, and it answers in natural conversation. Uses your own API key; chat usage is light, typically a few cents a day.",
 "zh": "选择模式（随时用 /mode 切换）：\n🆓 免费模式：基于实时数据表的即时回答（推荐、资金费率、余额、交易），永久免费，无需设置。\n✨ 付费模式（Claude / ChatGPT / Gemini / Grok）：您在 Telegram 里输入的内容会原样连同实时数据一起发给所选 AI，由它自然对话回答。使用您自己的 API 密钥，用量很小，通常每天几美分。",
 "ja": "モードを選んでください（/mode でいつでも切替可）：\n🆓 無料モード：リアルタイムデータ表による即答（ピック・資金調達率・残高・取引）。ずっと無料、設定不要。\n✨ 有料モード（Claude / ChatGPT / Gemini / Grok）：Telegram に打った内容がそのまま実データと一緒に選んだ AI へ送られ、自然な会話で答えます。ご自身の API キーを使用、通常は一日数セントです。",
 "ko": "모드를 선택하세요 (/mode 로 언제든 변경):\n🆓 무료모드: 실시간 데이터표 기반 즉답 (픽·펀딩·잔고·거래). 계속 무료, 설정 불필요.\n✨ 유료모드 (클로드 / GPT / 제미나이 / 그록): 텔레그램에서 말하는 그대로 실데이터와 함께 선택한 AI에 전달되고, 그 AI가 자연스러운 대화로 답합니다. 본인 API 키 사용, 대화 사용량은 적어 보통 하루 몇 센트.",
 "vi": "Chọn chế độ (đổi bằng /mode bất kỳ lúc nào):\n🆓 Miễn phí: trả lời tức thì từ bảng dữ liệu trực tiếp. Luôn miễn phí.\n✨ Trả phí (Claude / ChatGPT / Gemini / Grok): những gì bạn gõ trong Telegram được gửi thẳng đến AI bạn chọn cùng dữ liệu thật, AI trả lời tự nhiên. Dùng API key của bạn; thường vài cent mỗi ngày.",
 "hi": "मोड चुनें (/mode से कभी भी बदलें):\n🆓 मुफ़्त: लाइव डेटा से तुरंत जवाब। हमेशा मुफ़्त।\n✨ पेड (Claude / ChatGPT / Gemini / Grok): Telegram में जो लिखें वह सीधे चुने हुए AI को जाता है और वह स्वाभाविक बातचीत में जवाब देता है। आपकी अपनी API key; आमतौर पर कुछ सेंट प्रतिदिन।",
 "id": "Pilih mode (ganti kapan saja dengan /mode):\n🆓 Gratis: jawaban instan dari tabel data langsung. Selalu gratis.\n✨ Berbayar (Claude / ChatGPT / Gemini / Grok): apa yang Anda ketik di Telegram diteruskan apa adanya ke AI pilihan bersama data langsung, dan AI menjawab secara alami. Memakai API key Anda sendiri; biasanya beberapa sen per hari.",
 "ru": "Выберите режим (смена в любое время: /mode):\n🆓 Бесплатный: мгновенные ответы из живых таблиц. Всегда бесплатно.\n✨ Платный (Claude / ChatGPT / Gemini / Grok): то, что вы пишете в Telegram, передаётся выбранному ИИ вместе с живыми данными, и он отвечает в живом диалоге. Ваш собственный API ключ; обычно несколько центов в день.",
 "pt": "Escolha o modo (troque quando quiser com /mode):\n🆓 Grátis: respostas instantâneas das tabelas de dados ao vivo. Sempre grátis.\n✨ Pago (Claude / ChatGPT / Gemini / Grok): o que você digita no Telegram vai direto para a IA escolhida junto com os dados reais, e ela responde em conversa natural. Sua própria chave de API; normalmente alguns centavos por dia.",
 "tr": "Mod seçin (/mode ile her zaman değiştirilebilir):\n🆓 Ücretsiz: canlı veri tablolarından anında yanıt. Her zaman ücretsiz.\n✨ Ücretli (Claude / ChatGPT / Gemini / Grok): Telegram'a yazdıklarınız canlı verilerle birlikte seçtiğiniz yapay zekaya aynen iletilir ve o doğal sohbetle yanıtlar. Kendi API anahtarınız; genellikle günde birkaç sent.",
 "es": "Elige el modo (cámbialo cuando quieras con /mode):\n🆓 Gratis: respuestas instantáneas de tablas de datos en vivo. Siempre gratis.\n✨ De pago (Claude / ChatGPT / Gemini / Grok): lo que escribes en Telegram va tal cual a la IA elegida junto con los datos reales, y responde en conversación natural. Con tu propia clave API; normalmente unos centavos al día.",
}
T["prov_pick"] = {
 "en": "Which AI do you want to use?", "zh": "想使用哪个 AI？",
 "ja": "どの AI を使いますか？", "ko": "어떤 AI를 쓰시겠어요?",
 "vi": "Bạn muốn dùng AI nào?", "hi": "कौन सा AI इस्तेमाल करना है?",
 "id": "AI mana yang ingin Anda pakai?", "ru": "Какой ИИ использовать?",
 "pt": "Qual IA você quer usar?", "tr": "Hangi yapay zekayı kullanmak istersiniz?",
 "es": "¿Qué IA quieres usar?"}
T["btn_free"] = {"en": "🆓 Free", "zh": "🆓 免费", "ja": "🆓 無料",
                 "ko": "🆓 무료", "vi": "🆓 Miễn phí", "hi": "🆓 मुफ़्त",
                 "id": "🆓 Gratis", "ru": "🆓 Бесплатно", "pt": "🆓 Grátis",
                 "tr": "🆓 Ücretsiz", "es": "🆓 Gratis"}
T["btn_paid"] = {c: "✨ AI" for c, _ in LANGS}
# ask_token takes .format(name, prefix, url): provider name, key prefix,
# and the page where the key is issued
T["ask_token"] = {
 "en": "Paste your {0} API key (starts with {1}). Get one at {2}. It is stored on the operator's server and used only to answer YOUR questions. Switch modes anytime with /mode.",
 "zh": "粘贴您的 {0} API 密钥（以 {1} 开头）。在 {2} 获取。密钥保存在运营者服务器上，仅用于回答您的问题。随时用 /mode 切换模式。",
 "ja": "{0} の API キー（{1} で始まる）を貼り付けてください。{2} で発行できます。キーは運営者のサーバーに保存され、あなたの質問への回答にのみ使われます。/mode でいつでも切替できます。",
 "ko": "{0} API 키({1} 로 시작)를 붙여넣으세요. {2} 에서 발급. 키는 운영자 서버에 저장되며 본인 질문 답변에만 쓰입니다. /mode 로 언제든 전환.",
 "vi": "Dán API key {0} của bạn (bắt đầu bằng {1}). Lấy tại {2}. Key được lưu trên máy chủ của nhà vận hành và chỉ dùng để trả lời câu hỏi của bạn. Đổi chế độ bằng /mode.",
 "hi": "अपनी {0} API key पेस्ट करें ({1} से शुरू)। {2} पर मिलेगी। /mode से कभी भी मोड बदलें।",
 "id": "Tempel API key {0} Anda (diawali {1}). Dapatkan di {2}. Key disimpan di server operator dan hanya dipakai menjawab pertanyaan ANDA. Ganti mode dengan /mode.",
 "ru": "Вставьте ваш {0} API ключ (начинается с {1}). Получить: {2}. Ключ хранится на сервере оператора и отвечает только на ВАШИ вопросы. Сменить режим: /mode.",
 "pt": "Cole a sua chave de API do {0} (começa com {1}). Obtenha em {2}. Fica no servidor do operador e só responde às SUAS perguntas. Troque de modo com /mode.",
 "tr": "{0} API anahtarınızı yapıştırın ({1} ile başlar). {2} adresinden alın. Anahtar operatörün sunucusunda saklanır ve yalnızca SİZİN sorularınız için kullanılır. /mode ile modu değiştirin.",
 "es": "Pega tu clave API de {0} (empieza con {1}). Consiguela en {2}. Se guarda en el servidor del operador y solo responde TUS preguntas. Cambia de modo con /mode.",
}
# token_saved takes .format(provider name)
T["token_saved"] = {
 "en": "Key saved. {0} mode is on. Just chat naturally.",
 "zh": "密钥已保存。{0} 模式已开启。", "ja": "キーを保存しました。{0} モードが有効です。",
 "ko": "키 저장 완료. {0} 모드가 켜졌습니다.", "vi": "Đã lưu key. Chế độ {0} đã bật.",
 "hi": "Key सहेजी गई। {0} मोड चालू।", "id": "Key tersimpan. Mode {0} aktif.",
 "ru": "Ключ сохранён. Режим {0} включён.", "pt": "Chave salva. Modo {0} ativado.",
 "tr": "Anahtar kaydedildi. {0} modu açık.", "es": "Clave guardada. Modo {0} activado."}
# token_bad takes .format(name, prefix)
T["token_bad"] = {
 "en": "That does not look like a {0} API key (should start with {1}). Try again, or /mode to switch.",
 "zh": "这不像 {0} API 密钥（应以 {1} 开头）。请重试，或用 /mode 切换。",
 "ja": "{0} の API キーではないようです（{1} で始まるはず）。もう一度か、/mode で切替を。",
 "ko": "{0} API 키가 아닌 것 같습니다 ({1} 로 시작). 다시 시도하거나 /mode 로 전환.",
 "vi": "Không giống API key {0} (phải bắt đầu bằng {1}). Thử lại hoặc /mode.",
 "hi": "यह {0} API key नहीं लगती ({1} से शुरू हो)। फिर कोशिश करें या /mode।",
 "id": "Sepertinya bukan API key {0} (harus diawali {1}). Coba lagi atau /mode.",
 "ru": "Не похоже на ключ {0} (должен начинаться с {1}). Попробуйте ещё раз или /mode.",
 "pt": "Não parece uma chave de API do {0} (deve começar com {1}). Tente de novo ou /mode.",
 "tr": "{0} API anahtarına benzemiyor ({1} ile başlamalı). Tekrar deneyin veya /mode.",
 "es": "No parece una clave API de {0} (debe empezar con {1}). Intentalo de nuevo o /mode."}
T["mode_now_free"] = {
 "en": "Free mode is on. Your saved key was removed.",
 "zh": "已切换到免费模式，已删除保存的密钥。", "ja": "無料モードにしました。保存されたキーは削除済みです。",
 "ko": "무료 모드로 전환했습니다. 저장된 키는 삭제됨.", "vi": "Đã chuyển sang miễn phí. Key đã lưu bị xóa.",
 "hi": "मुफ़्त मोड चालू। सहेजी key हटा दी गई।", "id": "Mode gratis aktif. Key yang tersimpan dihapus.",
 "ru": "Включён бесплатный режим. Сохранённый ключ удалён.", "pt": "Modo grátis ativado. A chave salva foi removida.",
 "tr": "Ücretsiz mod açık. Kayıtlı anahtar silindi.", "es": "Modo gratis activado. La clave guardada fue eliminada."}
T["menu_extra"] = {
 "en": "/menu - this list\n/mode - switch Free / AI mode\nOr just type naturally - commands are optional.",
 "zh": "/menu - 命令列表\n/mode - 切换免费 / AI 模式\n也可直接自然输入，命令可选。",
 "ja": "/menu - コマンド一覧\n/mode - 無料 / AI 切替\n普通に打っても回答します。",
 "ko": "/menu - 명령어 목록\n/mode - 무료 / AI 모드 전환\n그냥 대화하듯 치셔도 됩니다.",
 "vi": "/menu - danh sách lệnh\n/mode - đổi Miễn phí / AI\nHoặc cứ gõ tự nhiên.",
 "hi": "/menu - सूची\n/mode - मुफ़्त / AI\nया बस सीधे लिखें।",
 "id": "/menu - daftar perintah\n/mode - ganti Gratis / AI\nAtau ketik biasa saja.",
 "ru": "/menu - список команд\n/mode - смена режима\nИли просто пишите.",
 "pt": "/menu - lista\n/mode - Grátis / AI\nOu escreva normalmente.",
 "tr": "/menu - liste\n/mode - Ücretsiz / AI\nYa da doğal yazın.",
 "es": "/menu - lista\n/mode - Gratis / AI\nO escribe con naturalidad."}


# Shown when someone picks a flag they had already picked once. The first
# pick starts onboarding; this one only changes the language, so it says so
# and stops there rather than re-asking for a wallet nobody offered to
# change. (08-28)
T["lang_changed"] = {
 "en": "✅ Language changed to English.",
 "zh": "✅ 语言已切换为中文。",
 "ja": "✅ 言語を日本語に変更しました。",
 "ko": "✅ 언어를 한국어로 바꿨습니다.",
 "vi": "✅ Đã đổi ngôn ngữ sang Tiếng Việt.",
 "hi": "✅ भाषा हिंदी में बदल दी गई।",
 "id": "✅ Bahasa diubah ke Bahasa Indonesia.",
 "ru": "✅ Язык изменён на русский.",
 "pt": "✅ Idioma alterado para Português.",
 "tr": "✅ Dil Türkçe olarak değiştirildi.",
 "es": "✅ Idioma cambiado a Español."}


# The install pitch, after the first time. A member gets nothing else from
# the central bot, so on every message the full pitch arrived again, which
# reads as spam to anyone who already installed, and the central bot has no
# way of knowing that they did: their own bot is a different bot they made
# themselves. So the long version goes once and the short one after.
# (08-28 user decision)
T["install_again"] = {
 "en": "Ocean Agent runs on your own machine with your own keys, so this "
       "shared bot has nothing of yours to show. → https://oceanagent.fi",
 "zh": "Ocean Agent 在你自己的电脑上用你自己的密钥运行，这个公共机器人没有"
       "你的数据可显示。 → https://oceanagent.fi",
 "ja": "Ocean Agent はあなたのPCであなたの鍵で動くため、この共用ボットに"
       "お見せできるものはありません。 → https://oceanagent.fi",
 "ko": "Ocean Agent 는 당신의 컴퓨터에서 당신의 키로 도는 제품이라 이 공용 "
       "봇에는 보여드릴 게 없습니다. → https://oceanagent.fi",
 "vi": "Ocean Agent chạy trên máy của bạn với khóa của bạn, nên bot chung "
       "này không có gì để hiển thị. → https://oceanagent.fi",
 "hi": "Ocean Agent आपके अपने कंप्यूटर पर आपकी अपनी कुंजी से चलता है, इसलिए इस "
       "साझा बॉट के पास दिखाने को कुछ नहीं है। → https://oceanagent.fi",
 "id": "Ocean Agent berjalan di komputer Anda dengan kunci Anda sendiri, "
       "jadi bot bersama ini tidak punya apa pun untuk ditampilkan. "
       "→ https://oceanagent.fi",
 "ru": "Ocean Agent работает на вашем компьютере с вашими ключами, поэтому "
       "этому общему боту нечего показать. → https://oceanagent.fi",
 "pt": "O Ocean Agent roda no seu computador com suas próprias chaves, "
       "então este bot compartilhado não tem nada para mostrar. "
       "→ https://oceanagent.fi",
 "tr": "Ocean Agent kendi bilgisayarınızda kendi anahtarlarınızla çalışır, "
       "bu yüzden bu ortak botun gösterecek bir şeyi yok. "
       "→ https://oceanagent.fi",
 "es": "Ocean Agent se ejecuta en tu propio equipo con tus propias claves, "
       "así que este bot compartido no tiene nada que mostrar. "
       "→ https://oceanagent.fi"}


def tier_kb(lang: str) -> str:
    return json.dumps({"inline_keyboard": [[
        {"text": tr("btn_free", lang), "callback_data": "tier:free"},
        {"text": tr("btn_paid", lang), "callback_data": "tier:paid"},
    ]]})


# paid-mode AI providers: key prefix for validation, issue page for the ask
PROVIDERS = {
    "claude": {"name": "Claude",  "prefix": "sk-ant-",
               "url": "console.anthropic.com > API keys"},
    "gpt":    {"name": "ChatGPT", "prefix": "sk-",
               "url": "platform.openai.com/api-keys"},
    "gemini": {"name": "Gemini",  "prefix": "AIza",
               "url": "aistudio.google.com/apikey"},
    "grok":   {"name": "Grok",    "prefix": "xai-",
               "url": "console.x.ai"},
}


def prov_kb() -> str:
    return json.dumps({"inline_keyboard": [[
        {"text": PROVIDERS[k]["name"], "callback_data": "prov:" + k}]
        for k in ("claude", "gpt", "gemini", "grok")]})


# ── /pick rendered from the live seal (2026-08-19) ───────────────────────
# pick_header takes the seal time; pick_row takes rank, symbol, side,
# expected move %, entry price.
T["pick_header"] = {
 "en": "📌 Picks (sealed {0})", "zh": "📌 推荐（封存于 {0}）",
 "ja": "📌 ピック（{0} 封印）", "ko": "📌 추천픽 ({0} 봉인)",
 "vi": "📌 Lựa chọn (chốt {0})", "hi": "📌 पिक्स ({0})",
 "id": "📌 Pilihan (disegel {0})", "ru": "📌 Подборка ({0})",
 "pt": "📌 Escolhas (selado {0})", "tr": "📌 Seçimler ({0})",
 "es": "📌 Selecciones (sellado {0})"}
# pick_row: rank, symbol, side, expected 24h move %, chance of touching
# +3% (the "long" side), chance of touching -3% (the "short" side), entry
T["pick_row"] = {
 "en": "{0}. {1} {2} · move {3}% · up {4}% / down {5}% · entry {6}",
 "zh": "{0}. {1} {2} · 波动 {3}% · 上涨 {4}% / 下跌 {5}% · 入场 {6}",
 "ja": "{0}. {1} {2} · 変動 {3}% · 上 {4}% / 下 {5}% · 参入 {6}",
 "ko": "{0}. {1} {2} · 예상변동 {3}% · 롱 {4}% / 숏 {5}% · 진입 {6}",
 "vi": "{0}. {1} {2} · biến động {3}% · lên {4}% / xuống {5}% · vào {6}",
 "hi": "{0}. {1} {2} · मूव {3}% · ऊपर {4}% / नीचे {5}% · एंट्री {6}",
 "id": "{0}. {1} {2} · gerak {3}% · naik {4}% / turun {5}% · masuk {6}",
 "ru": "{0}. {1} {2} · ход {3}% · вверх {4}% / вниз {5}% · вход {6}",
 "pt": "{0}. {1} {2} · movim. {3}% · alta {4}% / baixa {5}% · entrada {6}",
 "tr": "{0}. {1} {2} · hareket {3}% · yukarı {4}% / aşağı {5}% · giriş {6}",
 "es": "{0}. {1} {2} · mov. {3}% · sube {4}% / baja {5}% · entrada {6}"}
# one-line note under the list. The pair shown is the 2h one the side is
# read off, so this says what it is and that the larger side wins. Both
# can be high at once: a bar can touch +1.0% and -1.0% in the same two
# hours, so the two do not add to 100.
T["pick_note"] = {
 "en": "Move = expected 24h range. Up/down = chance of reaching ±1.0% "
       "within 2h; the larger side is the side we take.",
 "zh": "波动＝预计24小时幅度。上涨/下跌＝2小时内触及±1.0%的概率，较大的一侧就是我们的方向。",
 "ja": "変動＝24時間の予想幅。上/下＝2時間以内に±1.0%へ到達する確率で、大きい方が建てる方向です。",
 "ko": "예상변동은 24시간 예상 폭입니다. 롱·숏 %는 2시간 안에 ±1.0%에 닿을 확률이고, 큰 쪽이 방향이 됩니다.",
 "vi": "Biến động = biên độ dự kiến 24h. Lên/xuống = xác suất chạm ±1.0% trong 2h; bên lớn hơn là hướng vào lệnh.",
 "hi": "मूव = 24 घंटे की अपेक्षित रेंज। ऊपर/नीचे = 2 घंटे में ±1.0% छूने की संभावना; जो बड़ा हो वही दिशा है।",
 "id": "Gerak = rentang perkiraan 24 jam. Naik/turun = peluang menyentuh ±1.0% dalam 2 jam; sisi yang lebih besar adalah arah posisi.",
 "ru": "Ход = ожидаемый диапазон за 24ч. Вверх/вниз = шанс достичь ±1.0% за 2ч; большая сторона и есть направление.",
 "pt": "Movim. = faixa esperada em 24h. Alta/baixa = chance de atingir ±1.0% em 2h; o lado maior é o lado que assumimos.",
 "tr": "Hareket = 24 saatlik beklenen aralık. Yukarı/aşağı = 2 saatte ±1.0% dokunma olasılığı; büyük olan taraf yönümüzdür.",
 "es": "Mov. = rango esperado en 24h. Sube/baja = probabilidad de tocar ±1.0% en 2h; el lado mayor es el lado que tomamos."}
# Shown only when a row had no 2h pair and fell back to the old 24h one.
T["pick_note_alt"] = {
 "en": "* this row shows the 24h ±3% rates because the 2h ones were "
       "unavailable.",
 "zh": "* 该行显示24小时±3%的概率，因为无法取得2小时数值。",
 "ja": "* この行は2時間の値が取れず、24時間±3%の確率を表示しています。",
 "ko": "* 표시된 줄은 2시간 값이 없어 24시간 ±3% 확률을 보여줍니다.",
 "vi": "* dòng này hiển thị tỷ lệ ±3% trong 24h vì không có giá trị 2h.",
 "hi": "* इस पंक्ति में 2 घंटे का मान न होने से 24 घंटे ±3% की दर दिखाई गई है।",
 "id": "* baris ini menampilkan angka ±3% 24 jam karena nilai 2 jam tidak tersedia.",
 "ru": "* в этой строке показаны ставки ±3% за 24ч, так как значения за 2ч недоступны.",
 "pt": "* esta linha mostra as taxas de ±3% em 24h porque as de 2h não estavam disponíveis.",
 "tr": "* bu satırda 2 saatlik değerler bulunmadığı için 24 saatlik ±3% oranları gösteriliyor.",
 "es": "* esta fila muestra las tasas de ±3% en 24h porque las de 2h no estaban disponibles."}
T["pick_none"] = {
 "en": "No seal yet. The bot writes one every hour once it is running.",
 "zh": "尚无推荐。机器人运行后每小时生成一次。",
 "ja": "まだピックがありません。ボットが動き出すと毎時作成されます。",
 "ko": "아직 봉인이 없습니다. 봇이 돌기 시작하면 매시간 만들어집니다.",
 "vi": "Chưa có lựa chọn. Bot sẽ tạo mỗi giờ khi chạy.",
 "hi": "अभी कोई पिक नहीं। बॉट चलने पर हर घंटे बनती है।",
 "id": "Belum ada pilihan. Bot membuatnya tiap jam saat berjalan.",
 "ru": "Пока нет подборки. Бот создаёт её каждый час во время работы.",
 "pt": "Ainda sem escolhas. O bot gera a cada hora quando está rodando.",
 "tr": "Henüz seçim yok. Bot çalışırken her saat oluşturur.",
 "es": "Aún no hay selecciones. El bot las genera cada hora al ejecutarse."}
T["side_long"] = {
 "en": "LONG", "zh": "做多", "ja": "ロング", "ko": "롱", "vi": "LONG",
 "hi": "LONG", "id": "LONG", "ru": "ЛОНГ", "pt": "LONG", "tr": "LONG",
 "es": "LARGO"}
T["side_short"] = {
 "en": "SHORT", "zh": "做空", "ja": "ショート", "ko": "숏", "vi": "SHORT",
 "hi": "SHORT", "id": "SHORT", "ru": "ШОРТ", "pt": "SHORT", "tr": "SHORT",
 "es": "CORTO"}
