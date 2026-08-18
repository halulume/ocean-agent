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
}

# Intent keywords, lowercase; matched as substrings of the lowercased text.
# English is always included as a base, the user's language is added on top.
INTENT = {
    "ko": {
        "pick": ["픽", "추천", "종목", "뭐 사", "뭐 잡", "뭐사", "뭐잡"],
        "carry": ["캐리", "차익", "자리", "알람"],
        "funding": ["펀딩"],
        "balance": ["잔고", "얼마 있", "돈", "계좌"],
        "trades": ["체결", "이력", "내역", "거래"],
        "bot": ["봇", "상태", "잘 돌", "돌아가"],
        "why": ["왜", "이유", "먹혔", "잃었", "졌"]},
    "en": {
        "pick": ["pick", "recommend", "what to buy", "what should i buy",
                 "suggestion"],
        "carry": ["carry", "arbitrage", " arb "],
        "funding": ["funding"],
        "balance": ["balance", "how much", "money", "account"],
        "trades": ["trade", "fill", "history"],
        "bot": ["bot", "status", "running"],
        "why": ["why", "reason", "lost", "won"]},
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


def intent_words(intent: str, lang: str):
    """Keyword list for one intent: English base plus the user's language."""
    out = list(INTENT.get("en", {}).get(intent, ()))
    if lang and lang != "en":
        out += INTENT.get(lang, {}).get(intent, ())
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
