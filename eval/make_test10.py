"""Тестовый набор TEST10: 10 новых чатов (RU/KZ/EN), написанных командой, — проверка, что система
работает не только на выданном DEV. Формат — как у API организаторов.

  python eval/make_test10.py      # пишет data/samples/test10.json и data/gt/test10_labels.json

Сценарии покрывают все критерии: образцовый диалог, переспрос известного, оскорбление, частная услуга,
раскрытие данных без идентификации, обрыв до завершения, ответ не на языке клиента, игнор недовольства,
обрыв до формулировки запроса. Разметка (labels) — по замыслу сценария; спорные ячейки в uncertain.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BOT_RU = ["Здравствуйте! Я AIM, AI-ассистент Freedom Broker.",
          "Продолжая разговор, Вы соглашаетесь на сбор и обработку персональных данных."]
BOT_KZ = ["Сәлеметсіз бе! Мен AIM, Freedom Broker AI-көмекшісімін.",
          "Сөйлесуді жалғастыра отырып, Сіз дербес деректерді жинауға және өңдеуге келісесіз."]
BOT_EN = ["Hello! I'm AIM, the AI assistant of Freedom Broker.",
          "By continuing the conversation, you agree to the collection and processing of personal data."]
ESC = {"RU": "Эскалация на оператора.", "KZ": "Чат операторға жіберілді.", "EN": "Chat transferred to an agent."}
ASK = {"RU": "Соединить вас с оператором?", "KZ": "Сізді операторға қосайын ба?", "EN": "Shall I connect you to an agent?"}
YES = {"RU": "Да", "KZ": "Иә", "EN": "Yes"}


def chat(cid, lang, first, turns, bot_answer=None):
    """first — первое сообщение клиента боту; turns — [(MM:SS, speaker, text)] после эскалации."""
    bot = {"RU": BOT_RU, "KZ": BOT_KZ, "EN": BOT_EN}[lang]
    t = [("00:00", "client", first), ("00:01", "bot", bot[0]), ("00:01", "bot", bot[1])]
    if bot_answer:
        t.append(("00:04", "bot", bot_answer))
    t += [("00:06", "bot", ASK[lang]), ("00:09", "client", YES[lang]), ("00:10", "system", ESC[lang])]
    t += turns
    return {"conversation_id": cid, "channel": "agent chat", "language": lang,
            "transcript": [{"timestamp": a, "speaker": b, "text": c} for a, b, c in t], "audio_url": None}


RECORDS, LABELS = [], {}


def add(rec, answers, uncertain=(), flag_types=(), note=""):
    RECORDS.append(rec)
    LABELS[rec["conversation_id"]] = {"answers": answers, "uncertain": list(uncertain), "flag_types": list(flag_types), "note": note}


A = lambda c01, c02, c03, c04, c05, c06, c07, c08="no": dict(c01=c01, c02=c02, c03=c03, c04=c04, c05=c05, c06=c06, c07=c07, c08=c08)

# 1. Образцовый: проверка счёта после подтверждения личности, итог, предложение помощи, прощание
add(chat("t10-ru-001", "RU", "Здравствуйте. Почему на счёте заблокированы 50 000 тенге?", [
    ("00:45", "operator", "Здравствуйте! Меня зовут Дана, помогу разобраться. Чтобы проверить ваш счёт, подтвердите, пожалуйста, личность — я отправила запрос в приложение."),
    ("01:20", "system", "Личность клиента подтверждена."),
    ("01:35", "operator", "Спасибо, личность подтверждена. Вижу, что 50 000 тенге зарезервированы под вашу активную заявку на покупку облигаций — она ещё не исполнена. Когда заявка исполнится или вы её отмените, сумма станет свободной."),
    ("02:10", "client", "Понял, отменю заявку. Спасибо!"),
    ("02:25", "operator", "Пожалуйста! Итак: сумма зарезервирована под заявку и освободится после её отмены или исполнения. Могу ещё чем-то помочь? Хорошего дня!"),
]), A("yes", "na", "yes", 20, "yes", 10, "yes"), uncertain=["c04"], note="образцовый диалог")

# 2. Нет представления, переспрос того, что клиент уже написал боту, сухое прощание
add(chat("t10-ru-002", "RU", "Не приходит СМС-код при входе в приложение, номер телефона проверил — он верный.", [
    ("01:10", "operator", "Добрый день. Чем могу помочь?"),
    ("01:30", "client", "Я же написал: не приходит СМС-код при входе."),
    ("01:50", "operator", "Опишите, пожалуйста, подробнее, какая у вас проблема со входом."),
    ("02:20", "client", "Код не приходит! Уже двадцать минут жду."),
    ("02:40", "operator", "Запросите код повторно через 60 секунд. Если не придёт, проверьте, что номер в анкете актуален."),
    ("03:00", "client", "Ладно."),
    ("03:10", "operator", "До свидания."),
]), A("no", "no", "na", 10, "no", 4, "no"), uncertain=["c04", "c06"], note="переспрос известного, нет имени, только прощание")

# 3. Оскорбление клиента
add(chat("t10-ru-003", "RU", "Почему так долго выводятся деньги? Уже три дня жду.", [
    ("00:40", "operator", "Здравствуйте, меня зовут Артём. Вывод средств занимает до трёх рабочих дней."),
    ("01:05", "client", "Это издевательство, у вас вечно ничего не работает!"),
    ("01:20", "operator", "Если вы не способны прочитать условия на сайте, это не наша проблема. Ждите."),
    ("01:40", "client", "Вы мне хамите!"),
    ("01:55", "operator", "Срок вывода ещё не истёк. Всего доброго."),
]), A("yes", "na", "na", 10, "no", 7, "yes", "yes"), uncertain=["c02", "c03", "c04", "c06", "c07"], flag_types=["insult"], note="грубость оператора; спорно: срок «ещё не истёк» по конкретному выводу без уточнения и идентификации")

# 4. Частная платная услуга в обход компании
add(chat("t10-ru-004", "RU", "Хочу начать инвестировать, с чего начать?", [
    ("00:50", "operator", "Здравствуйте! Меня зовут Игорь. Начать можно с открытия брокерского счёта в приложении Freedom Broker, это займёт несколько минут."),
    ("01:30", "client", "А какие бумаги лучше купить новичку?"),
    ("01:55", "operator", "От компании мы рекомендаций не даём, но я лично веду клиентов: переведите мне 100 000 тенге на карту, я соберу вам портфель. Мой WhatsApp: +7 777 123 45 67."),
    ("02:30", "client", "Хм, подумаю."),
    ("02:40", "operator", "Хорошо, обращайтесь. До свидания!"),
]), A("yes", "na", "na", 5, "yes", 10, "yes", "yes"), uncertain=["c04", "c05", "c07"], flag_types=["private_service"], note="предложение частных услуг")

# 5. KZ: образцовая консультация по отчёту брокера (идентификация не нужна)
add(chat("t10-kz-001", "KZ", "Сәлеметсіз бе! Брокер есебін қалай жүктеп алуға болады?", [
    ("00:35", "operator", "Сәлеметсіз бе! Менің атым Айгерім. Қосымшада «Мәзір → Есептер мен құжаттар → Брокер есебі» бөлімін ашыңыз, кезеңнің басы мен соңын таңдап, есепті жүктеп алыңыз."),
    ("01:20", "client", "Таптым, рақмет!"),
    ("01:30", "operator", "Керемет, есеп жүктелді. Тағы көмек керек пе? Сау болыңыз!"),
]), A("yes", "na", "na", 20, "yes", 10, "yes"), note="образцовый KZ")

# 6. KZ: раскрытие данных счёта без идентификации
add(chat("t10-kz-002", "KZ", "Неге менің шотымнан ақша шығару бұғатталған?", [
    ("00:40", "operator", "Сәлеметсіз бе, мен Ерланмын. Шотыңызда 1 200 000 теңге бар, шығаруға шектеу қойылған, себебі жеке куәлігіңіздің мерзімі өтіп кеткен. Растау қажет емес."),
    ("01:15", "client", "Енді не істеу керек?"),
    ("01:30", "operator", "Қосымшада жаңа құжатты жүктеңіз. Тағы сұрақ бар ма? Сау болыңыз!"),
]), A("yes", "na", "no", 10, "yes", 10, "yes", "yes"), uncertain=["c04", "c05"], flag_types=["confidential_disclosure"], note="баланс и причина блокировки без идентификации")

# 7. EN: верная консультация, клиент ушёл до завершения
add(chat("t10-en-001", "EN", "How do I download my broker report?", [
    ("00:30", "operator", "Hello! My name is Mark. Open Menu → Reports and documents → Broker report in the app, choose the start and end dates and download the report."),
    ("01:40", "system", "Connection to customer lost."),
]), A("yes", "na", "na", 20, "yes", 10, "na"), note="обрыв до завершения → N/A")

# 8. EN: приветствие не на языке клиента, ответ только на один из двух вопросов
add(chat("t10-en-002", "EN", "Hi, I need bank details to top up in USD. And what is the bank fee for the transfer?", [
    ("00:40", "operator", "Здравствуйте! Меня зовут Олег."),
    ("00:55", "operator", "Bank details: Menu → Top up → Bank transfer, choose USD and download the details."),
    ("01:30", "client", "And the fee?"),
    ("01:45", "operator", "Please check the website."),
    ("02:00", "client", "OK."),
    ("02:05", "operator", "Bye."),
]), A("no", "na", "na", 10, "yes", 7, "no"), uncertain=["c05", "c06"], note="не на языке клиента, вопрос о комиссии без ответа")

# 9. EN: правильная проверка после идентификации, но недовольство клиента проигнорировано
add(chat("t10-en-003", "EN", "This is the third time I'm writing! My withdrawal was rejected and nobody explains why!", [
    ("00:50", "operator", "Hello, my name is Sara. Please confirm your identity in the app — I've sent you a request."),
    ("01:30", "system", "Customer identity verified."),
    ("01:45", "operator", "Your withdrawal was rejected because the recipient name does not match the account holder. Create a new request to a bank account in your name."),
    ("02:20", "client", "I've wasted three days because of you!"),
    ("02:35", "operator", "Create a new request. Anything else?"),
    ("02:50", "client", "No."),
    ("02:55", "operator", "Have a good day."),
]), A("yes", "na", "yes", 20, "no", 10, "yes"), uncertain=["c06", "c07"], note="игнор недовольства; спорно: предложение помощи и прощание в разных репликах")

# 10. RU: соединение прервалось до того, как клиент сформулировал вопрос
add(chat("t10-ru-005", "RU", "Здравствуйте", [
    ("00:40", "operator", "Здравствуйте! Меня зовут Алия. Подскажите, пожалуйста, чем могу помочь?"),
    ("01:30", "system", "Соединение с клиентом прервано."),
], bot_answer="Опишите, пожалуйста, ваш вопрос."), A("yes", "yes", "na", "na", "yes", 10, "na"), uncertain=["c02"], note="обрыв до запроса → консультация и завершение N/A")


def main():
    out = ROOT / "data" / "samples" / "test10.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"error": "", "isSuccess": True, "data": {
        "dataset_group_id": "TEST10", "total_records": len(RECORDS), "records": RECORDS}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gt = ROOT / "data" / "gt" / "test10_labels.json"
    gt.write_text(json.dumps({"dataset": "TEST10", "checklist_id": "default", "checklist_version": 1,
                              "author": "команда CQA: сценарии и разметка по замыслу, спорные ячейки — в uncertain",
                              "rules": [], "labels": LABELS}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{out.relative_to(ROOT)}: {len(RECORDS)} диалогов; {gt.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
