# Автоконтент для YouTube и Instagram в 2026 году: рынок, правила площадок, экономика, технический стек и юридическая база для оператора из РФ

**Дата среза:** 26.09.2026
**Формат:** рыночная аналитика. Источники: справки и блоги платформ, документация API, тексты законов, судебные акты, GitHub API, отчёты практиков (Reddit, форумы YouTube Help, searchengines.guru, vc.ru, Habr).

**Обозначения в тексте:**
- **(не подтверждено)** — утверждение не удалось сверить с первоисточником;
- **(оценка)** — расчёт или прикидка, а не опубликованные данные;
- **(исправлено)** — формулировку вторичного источника поправили при проверке по первоисточнику;
- **данные вендора** — цифры агентств и SaaS о самих себе, независимо не проверены.

**Ограничения сбора.** Лимит веб-поиска в сессиях исследования был исчерпан, поэтому страницы читались напрямую. Reddit — через RSS и архив pullpush.io. BlackHatWorld, Telega.in, Collabstr, vidIQ закрыты Cloudflare или капчей.

---

## 0. Коротко: главные выводы

1. **Перезалив чужих роликов «с уникализацией» на YouTube упирается сразу в три независимых механизма.** Первый — Content ID: 2,5 млрд заявок за 2025 год, больше 90% из них правообладатели монетизируют в свою пользу. Второй — правило YPP о reused/inauthentic content: оно действует «even if you have permission from the original creator», и оценивается канал целиком. Третий — спам-политика Community Guidelines, где ускорение аудио, фильтры, кроп и зеркалирование прямо названы «detection evasion». Третий механизм — это страйки и удаление канала, а не просто отказ в монетизации.
2. **Instagram с 30.04.2026 исключает из рекомендаций весь аккаунт**, если за скользящие 30 дней большинство его фото, каруселей и Reels неоригинальны. Рамки, водяные знаки, субтитры, смена скорости и подпись с автором официально не считаются переработкой. Rights Manager и content protection Meta ищут полные и частичные совпадения, в том числе задним числом.
3. **Санкции применяются к сети, а не к отдельному каналу.** После удаления одного канала владельцу запрещены все остальные (circumvention). Исключение из YPP распространяется на связанные каналы («related channel»). Google Research описал систему, которая за полгода удалила 130 тыс. каналов в 50 тыс. связанных кластерах (это научная публикация, не отчёт модерации).
4. **Итоги волн 2025–2026 годов.** 18.12.2025 удалены Screen Culture и KH Studio (2 млн+ подписчиков, 1 млрд+ просмотров). К 29.01.2026 удалены или очищены 16 крупных AI-slop-каналов (35 млн подписчиков, 4,7 млрд просмотров). За 9 месяцев 2025 года удалено 12,46 млн каналов. С марта по июль 2026 года прошла волна отключений монетизации за inauthentic content, в том числе у каналов на 250–600 тыс. подписчиков.
5. **Shorts почти не приносят денег.** Реалистично $10–50 за 1 млн публичных просмотров (TubeBuddy: $25–45). RPM на 1000 engaged views в 2025 году: США ~$0,33, Германия ~$0,16, Индия ~$0,008 (AIR). С 01.02.2027 долю пула Shorts получит только канал с ≥10 млн квалифицированных просмотров Shorts за 90 дней. Новому каналу для входа в YPP понадобится 8000 часов просмотра или 20 млн просмотров Shorts.
6. **Instagram не делится рекламной выручкой за Reels.** Деньги там — это брендированный контент, Gifts и бонусы по приглашению, а неоригинальный контент монетизировать запрещено. Facebook платит (≈$3 млрд авторам за 2025 год, 60% из них за Reels), но только по приглашению и только за оригинальный контент.
7. **Для оператора из РФ рекламная выручка YouTube закрыта.** Новые подключения к YPP из России на паузе с 10.03.2022, российские AdSense отключены в августе 2024 года, российским зрителям реклама Google не показывается. С 10–12.02.2026 YouTube удалён из НСДИ и доступен только через VPN, хотя по Mediascope его всё равно смотрят ~22 млн человек в день. YPP требует, чтобы автор реально жил в стране программы.
8. **Реклама в Instagram в РФ запрещена с 01.09.2025** (ч. 10.7 ст. 5 38-ФЗ, закон 72-ФЗ), первые штрафы выписаны в октябре 2025 года. Рекламу в YouTube и Telegram ФАС тоже считает нарушением этой нормы, но до 31.12.2026 не штрафует.
9. **«Вшитый баннер» как модель.** YouTube запрещает вшивать в ролик готовые рекламные ролики и рекламные паузы сторонних рекламодателей. Meta прямо запрещает «banner ads in videos». Разрешена интеграция, которую делает сам автор, с раскрытием (paid promotion на YouTube, branded content tool у Meta). В РФ RUTUBE допускает рекламные врезки с erid поверх видео.
10. **Легальные источники контента в большом объёме существуют**, но деньги в них идут не через рекламу YouTube. Это клиппинг-кампании (Whop Content Rewards, Clipping.net, Vyro, в РФ Klipni; $0,2–6 за 1000 просмотров, в среднем около $1), лицензии на вирусные видео (Newsflare от $230 за ролик, только редакционное использование), собственный архив правообладателя, CC BY, общественное достояние, стоки. Для резидента РФ западные клиппинг-биржи фактически закрыты: Whop не проверяет личность людей в России, а Stripe, PayPal и Wise Россию не обслуживают.
11. **Лицензия защищает от заявок и страйков, но не открывает YPP.** Правило о reused content «not based on copyright, permission, or fair use». Для рекламной монетизации нужен собственный вклад: комментарий, сюжет, разбор, лицо или голос автора.
12. **AI/faceless-производство дешёвое.** Ролик на 60 секунд из стоков с TTS стоит от долей цента до ~$0,10, полностью AI-видео — от ~$3 до ~$24 за минуту без учёта перегенераций. Ограничения: правило о шаблонном контенте (в июле 2026 года разбито на три категории), автоматические AI-метки YouTube с 27.05.2026 и метка AI-профиля в Instagram с 31.08.2026.
13. **Конвейер нарезки технически решённая и дешёвая задача.** Для русского ASR есть GigaAM-v3 (MIT, средний WER 8,4% против 25,1% у Whisper large-v3 по данным разработчика), плюс WhisperX, MediaPipe, ffmpeg/libass. На GPU обработка часа исходника занимает ~5–8 минут и стоит меньше $0,10; у SaaS — $3–15 (оценка).
14. **Автозалив возможен только через официальные API или аудированных посредников.** YouTube по умолчанию даёт 100 загрузок в сутки на проект, а без аудита все ролики навсегда остаются private. Instagram — 100 постов за 24 часа (в части документации 50). TikTok без аудита публикует только private, а собственные утилиты для постинга в свои аккаунты его правила прямо запрещают. Посредники: Upload-Post ($24–438 в месяц), Zernio ($1–6 за аккаунт).
15. **В РФ реально платят VK Видео и Rutube.** VK Видео: Revenue Share 50–80%, с 01.09.2026 вход от 1000 подписчиков, нужен оригинальный горизонтальный контент; авторы получили >3 млрд ₽ за 2025 год. Rutube: 1,5 млрд ₽ за 2025 год, уникальный видеоряд обязателен. Кроме того, есть VK AdBlogger и Яндекс ПроБлогер (реклама и CPA с автоматической маркировкой). Обязательны erid, сбор 3% и регистрация в реестре РКН для страниц с аудиторией от 10 тыс.

---

## 1. Как устроен рынок автоконтента в 2026 году

### 1.1 Четыре модели

| Модель | Откуда контент | Кто платит | Порядок денег | Главный ограничитель |
|---|---|---|---|---|
| **Перезалив / агрегатор** | Вирусные ролики с других платформ, фильмы и ТВ, стримы, посты Reddit | Не YPP: продакт-плейсмент, партнёрские ссылки, продажа рекламы в Telegram-сетках, CPA | При заявке Content ID доход получает правообладатель; неоригинальные Shorts исключены из пула | Content ID, reused/inauthentic, спам-политика, Rights Manager, детекция сетей |
| **Клиппинг по кампаниям** | Исходники выдаёт бренд, стример или подкаст | Заказчик кампании за верифицированные просмотры | $0,2–6 за 1000, в среднем ~$1 | Заказчик одобряет каждый ролик, кампании ограничены по регионам, из РФ выплаты недоступны |
| **Свой контент + нарезка** | Собственный архив или архив партнёра по договору | YPP (длинные видео), интеграции, партнёрские программы VK и Rutube | Медианный RPM длинных видео ~$2,3, у Shorts — центы | Нужен объём оригинального производства |
| **AI/faceless-генерация** | LLM + TTS + стоки или видео-модели | YPP, Facebook, интеграции | Себестоимость от центов до ~$24 за минуту | Шаблонность (inauthentic), AI-персоны, автоматические метки |

### 1.2 Кто на чём зарабатывает (цифры)

**Платформы.** YouTube отдаёт автору 55% выручки длинных видео и 45% выделенной ему доли пула Shorts ([YouTube Help 12504220](https://support.google.com/youtube/answer/12504220?hl=en)). В ленте Shorts больше 200 млрд просмотров в день ([YouTube blog, 10.08.2026](https://blog.youtube/news-and-events/youtube-partner-program-updates-2027-new-opportunities-earn/)). Facebook выплатил авторам в 2025 году почти $3 млрд, из них 60% за Reels ([Meta, 30.03.2026](https://creators.facebook.com/blog/new-tips-to-help-you-earn-on-facebook-content-monetization)).

**Правообладатели.** Через Content ID выплачено больше $12 млрд с момента запуска (по конец 2024 года). Из 2,5 млрд заявок 2025 года больше 90% монетизируются в пользу правообладателя ([TorrentFreak, 06.06.2026](https://torrentfreak.com/youtube-processed-2-5-billion-content-id-copyright-claims-in-2025/)). Агентства вирусного видео зарабатывают на эксклюзивных правах: у ViralHog в каталоге 79 951 ролик, продавцам выплачено 24,4 млн (валюта на сайте не указана), а ролики агентство монетизирует на YouTube само ([viralhog.com](https://viralhog.com/)).

**Клиппинг-маркетплейсы и агентства.** Clipping.net заявляет $60M+ выплат, 77 000+ клипперов и 200+ кампаний ([clipping.net](https://clipping.net), данные вендора). Бюджет Whop Clips — $190 000 в месяц ([Whop blog](https://whop.com/blog/content-rewards/)). По данным [NPR (12.05.2026)](https://www.npr.org/2026/05/12/nx-s1-5794670/influencers-creators-video-clips), у Эмраха Байрактара сеть из 40 000 клипперов в основном 16–24 лет, 19-летний Bo Lucenko зарабатывает около $4000 в месяц, а Cluely наняла 700+ клипперов. Стример N3on, по его словам, платит клипперам около $1M в месяц ([Prof G, 14.04.2026](https://www.profgmedia.com/p/the-clip-economy); не подтверждено). В СНГ Мелстрой, по заявлению автора статьи vc.ru, заплатил нарезчикам около $30 млн за два года ([vc.ru](https://vc.ru/2918998); не подтверждено).

**Спонсорские агентства.** ThoughtLeaders: 10,2 тыс. верифицированных авторов, 25 тыс. кампаний, $60M+ выплат, для авторов без комиссии ([thoughtleaders.io](https://www.thoughtleaders.io/for-creators), данные вендора).

**AI-slop-сегмент.** По отчёту [Kapwing (28.11.2025)](https://www.kapwing.com/blog/ai-slop-report-the-global-rise-of-low-quality-ai-videos/), 278 каналов публикуют только AI-slop, у них 63 млрд просмотров и оценочно около $117 млн в год. Из первых 500 Shorts, показанных новому аккаунту, 104 (21%) были сгенерированы ИИ, 165 (33%) — «brainrot». У лидера по просмотрам, Bandar Apna Dost, 2,07 млрд просмотров и оценочно ~$4,25 млн в год. Все доходы в отчёте — оценки третьей стороны.

**Российский рынок.**
- VK Видео: авторы заработали >3 млрд ₽ за 2025 год, средний заработок участника партнёрской программы вырос в 6,4 раза (исправлено: в 6,4 раза вырос средний заработок, а не доход программы) ([Habr](https://habr.com/ru/articles/1026868/)).
- VK Донат: 2,23 млрд ₽ по всему ВКонтакте. VK AdBlogger: 1,145 млрд ₽ (500 млн ₽ за посевы и контент на заказ плюс 645 млн ₽ за шопсы) ([vk.ru/monetization](https://vk.ru/monetization)).
- Rutube: 1,5 млрд ₽ авторам за 2025 год ([t.me/rutube/5032](https://t.me/rutube/5032)).
- Яндекс ПроБлогер: заявляет 6+ млрд ₽ выплат авторам ([partner.yandex.ru/messengers](https://partner.yandex.ru/messengers), данные вендора).
- Рынок рекламы в Telegram — больше 90 млрд ₽ в год (АКАР и Telega.in, апрель 2026, через [vc.ru](https://vc.ru/2974687)).
- Рекламный сбор 3% за апрель–сентябрь 2025 года — 7,8 млрд ₽. Рынок интернет-рекламы по оценке РКН — 858,6 млрд ₽ ([CNews, 16.01.2026](https://www.cnews.ru/news/top/2026-01-16_za_polgoda_rynok_zaplatil)).

### 1.3 Модель перезалива как бизнес: как она устроена на уровне рынка

**Предложение.** Операторы отслеживают вирусный контент на одних площадках и перезаливают его на другие, часто сразу в несколько каналов. Стормчейзер в r/PartneredYoutube (27.01.2026) описывает организованные сети во Вьетнаме, Пакистане, Индии и России, которые автоматически находят вирусные ролики и перезаливают их. По его оценке, так уходит около 80% его дохода ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1qo6l2l/organized_international_piracy_cartels/)).

**Монетизация.** Для YPP такой контент заведомо не проходит, поэтому зарабатывают вне программы. [Tubefilter (июль 2025)](https://www.tubefilter.com/2025/07/03/youtube-crackdown-repetitive-non-original-content/) приводит два примера. Канал New Man Magic занял 30-е место в Global Top 100 за июнь 2025 года и набирал около 1,7 млн подписчиков в месяц: он перезаливал чужие видео с изменёнными названиями, где рекламировался товар «VitaWear Smart Band». Канал Oren Shorts (69-е место, +1,2 млн подписчиков в месяц) выпускал однотипные Shorts с персонажами «Italian Brainrot». Судя по всему, доход таких каналов — продакт-плейсмент и партнёрские ссылки, а не AdSense.

**Российский сегмент — воронка в Telegram.** Shorts, Reels или TikTok ведут в Telegram-каналы и ботов: кино-боты с обязательной подпиской на 3+ канала, «сигналы» ставок, дейтинг-прелендинги. Деньги приносят продажа рекламы в сетке TG-каналов, продажа самих каналов и CPA. Ниши по гайду 2026 года: гемблинг, адалт под видом фитнеса, крипта, инфопродукты, товарка ([cpa.rip](https://cpa.rip/youtube/youtube-shorts-traffic-monetizing/), [vc.ru, 11.08.2026](https://vc.ru/3073614)). Конверсия низкая: «за 20 000 просмотров может не быть ни одного перехода, хотя иногда достигает 40» ([vc.ru, 18.01.2025](https://vc.ru/1761968)). На searchengines.guru ещё в 2024 году отмечали, что нарезки в Shorts монетизируются баннерами ставок и казино, а не партнёрской программой YouTube ([searchengines.guru](https://searchengines.guru/ru/forum/1103844)). Ревшар букмекеров «около 50%» в источниках не найден (не подтверждено).

**Сторона правообладателя.** Если перезалив попал в Content ID, ролик чаще всего остаётся в доступе, но реклама с него идёт правообладателю. На уровне рынка перезаливщик становится бесплатным дистрибьютором для владельца прав. Агентства вроде ViralHog строят на этом свой бизнес: получают эксклюзивные права на вирусные ролики и монетизируют их на YouTube.

**Жизненный цикл, который описывают практики и платформы:** быстрый рост → заявки и отключение монетизации → попытка вернуться с косметическими изменениями → удаление канала и связанных каналов. Самый наглядный пример — Screen Culture и KH Studio (подробнее в разделе 3).

### 1.4 Легальная клип-экономика

Клипы работают как дистрибуция: по данным [Prof G](https://www.profgmedia.com/p/the-clip-economy), клипы TBPN в среднем набирают 257 тыс. просмотров против 7 тыс. у эфира, у Hasan Piker — 700 тыс.+ против 33 тыс. Kick и MrBeast запустили собственные программы клиппинга. NPR отмечает, что платформы одновременно дают клипам алгоритмический охват и давят на страницы, где один и тот же момент дублируется множеством копий: «activity that can appear spammy or bot-like».

YouTube сам встраивает нарезку в продукт. На Made on YouTube 2025 (16.09.2025) объявили ИИ-клипы из подкастов, на Made on YouTube 2026 (23.09.2026) — диалоговый ИИ-редактор для Shorts, A/B-тесты до трёх вариантов монтажа и Shorts Series ([blog.youtube](https://blog.youtube/news-and-events/innovation-youtube-era-made-on-viewers-creators/)). Правообладатели всё чаще нарезают свой контент сами, внутри платформы.

### 1.5 Размер аудитории, важный для выбора площадок

- **YouTube в РФ**: 65,9 млн в месяц и 22 млн в день (Mediascope, январь 2026, десктоп и мобильные). За январь–май 2026 года дневной охват вырос на 4%, до 22,4 млн ([Коммерсант](https://www.kommersant.ru/doc/8798352)).
- **VK Видео**: 82,8 млн MAU и 42,2 млн DAU (январь 2026) ([yagla.ru](https://yagla.ru/blog/socialnye-seti/mediascope-vk-video-obognal-youtube-po-ejednevnoy-auditorii-v-rossii-v-yanvare--2602u106040/)).
- **Rutube**: сам сервис заявляет MAU больше 82–85 млн. DAU по Mediascope за январь 2026 года (в версии «Газпром-Медиа») — около 20 млн. По «Коммерсанту» со ссылкой на «Родную речь», дневной охват за январь–май 2026 года упал на 13,6%, до 7,2 млн. Цифра «DAU 26 млн» не подтверждена.
- **Telegram**: 93,6 млн в месяц (конец 2025 года). С 10.02.2026 мессенджер официально замедляют ([vc.ru](https://vc.ru/3046418)).
- **Мир**: по YouTube, в среднем больше двух третей времени просмотра у автора приходится на зрителей за пределами его страны ([YouTube Help 4792576](https://support.google.com/youtube/answer/4792576?hl=en)).

---

## 2. Как YouTube и Instagram распознают перезаливы и неоригинальный контент и что за это бывает

### 2.1 Хронология изменений, важных для модели

| Дата | Событие |
|---|---|
| 31.08.2023 | YouTube: ссылки в описаниях и комментариях Shorts перестают быть кликабельными |
| 30.04.2024 | Instagram: аккаунт, который 10+ раз за 30 дней публикует чужие Reels без переработки, не рекомендуется |
| 15.10.2024 | YouTube: Shorts длиной до 3 минут |
| 30.10.2024 | YouTube API: поле `status.containsSyntheticMedia` |
| 19.03.2025 | YouTube: удаление контента с упоминанием, логотипами или рекламой несертифицированных казино |
| 31.03.2025 | YouTube: публичный счётчик Shorts считает каждый старт; для выплат остаются «engaged views» |
| 24.04.2025 | Facebook: санкции против спам-сетей (показ только подписчикам, демонетизация) |
| 14.07.2025 | Facebook: демонетизация и урезание охвата за неоригинальный контент |
| 15.07.2025 | YouTube: «repetitious content» переименован в «inauthentic content» |
| 01.09.2025 | РФ: запрет рекламы в Instagram и Facebook (72-ФЗ) |
| 09.10.2025 | YouTube: пилот «second chance» для удалённых авторов |
| 17.11.2025 | Meta: content protection для Reels (поиск копий на FB и IG) |
| 04.12.2025 | YouTube API: загрузка дешевеет с ~1600 до ~100 единиц квоты |
| 18.12.2025 | Удалены Screen Culture и KH Studio |
| 21.01.2026 | Письмо CEO YouTube: борьба с «low quality AI content» — приоритет 2026 года |
| 29.01.2026 | Kapwing: 16 каналов из топ-100 AI-slop удалены или очищены |
| 10–12.02.2026 | РФ: домены YouTube удалены из НСДИ |
| 12–13.03.2026 | Facebook: новое определение неоригинального контента |
| 25.03.2026 | ФАС: до 31.12.2026 не штрафует за рекламу в Telegram и YouTube |
| 30.04.2026 | Instagram: правило оригинальности распространено на фото и карусели |
| 17–18.05.2026 | YouTube: likeness detection открыт всем 18+ |
| 27.05.2026 | YouTube: автоматические AI-метки по C2PA, SynthID и внутренним сигналам |
| 01.06.2026 | YouTube API: отдельная квота на загрузки (100 в сутки по умолчанию) |
| 13–20.07.2026 | YouTube: разъяснение трёх немонетизируемых категорий inauthentic content |
| 10.08.2026 | YouTube: анонс новых порогов YPP с 01.02.2027 |
| 24.08.2026 | YouTube: новый подсчёт публичных просмотров для всех форматов |
| 31.08.2026 | Instagram: метка «AI-generated profile»; вступает в силу Branded Content Policy TikTok |
| 01.09.2026 | VK Видео: вход в партнёрскую программу от 1000 подписчиков; вступает в силу 283-ФЗ о криптовалюте; анонс монетизации Rutube Shorts |
| 23.09.2026 | Made on YouTube: к likeness detection добавят распознавание голоса |
| 24.09.2026 | YouTube: новые Shorts длиной 1–3 минуты с заявкой Content ID больше не блокируются автоматически; закрыт Sora 2 API |
| 01.02.2027 | YouTube: новые пороги YPP и порог 10 млн просмотров для дохода из пула Shorts |

### 2.2 YouTube, уровень 1: авторское право (Content ID, Copyright Match Tool, страйки)

**Как работает Content ID.** Система автоматически сверяет каждую загрузку с базой аудио- и видеоэталонов правообладателей. При совпадении правообладатель выбирает одно из трёх действий: заблокировать ролик, монетизировать его в свою пользу (иногда с долей для загрузившего) или отслеживать статистику. Действие можно задавать отдельно по странам ([YouTube Help 2797370](https://support.google.com/youtube/answer/2797370?hl=en), [6013276](https://support.google.com/youtube/answer/6013276?hl=en)). Доступ к системе получают только владельцы «exclusive rights to a substantial body of original material that is frequently uploaded to YouTube». Эталоном не могут быть компиляции, «best of», ремиксы, неэксклюзивно лицензированный контент и геймплей ([YouTube Help 1311402](https://support.google.com/youtube/answer/1311402?hl=en)). Поэтому канал, собранный на чужом или неэксклюзивном материале, не сможет защищать через Content ID собственные ролики.

**Статистика 2025 года** ([TorrentFreak, 06.06.2026](https://torrentfreak.com/youtube-processed-2-5-billion-content-id-copyright-claims-in-2025/)):
- 2 502 941 368 заявок, на 14% больше, чем в 2024 году (2,2 млрд);
- оспорено 12 840 608 заявок (0,51%), 67,42% споров решены в пользу загрузившего (в 2024 году — 70%);
- к системе допущено 7626 правообладателей, активны 4454;
- Copyright Match Tool пользовались 173 338 авторов;
- больше 90% заявок монетизируются в пользу правообладателя.

(Исправлено.) Цифра «99,5% обработано автоматически» неверна по смыслу. Источник говорит другое: на активных правообладателей Content ID приходится 99,48% всех действий по авторскому праву, а около 99% всех заявок о нарушении на YouTube проходят через Content ID, а не через веб-форму и другие механизмы.

**Copyright Match Tool** доступен участникам YPP. Он находит полные перезаливы их видео на других каналах и позволяет одним действием запросить удаление. Для бизнеса на чужом контенте это прямой источник страйков.

**Страйки** ([YouTube Help 2814000](https://support.google.com/youtube/answer/2814000?hl=en)):
- три страйка за 90 дней: «Your account, along with any associated channels, is subject to termination» и «You can't create new YouTube channels»;
- страйк снимается через 90 дней, только если пройдена Copyright School;
- если правообладатель подал отложенный запрос на удаление, есть 7 дней, чтобы удалить видео самому и избежать страйка;
- если у связанного канала три активных страйка, удалить могут и ваш канал;
- заявка Content ID сама по себе обычно не даёт страйка, но если оспорить её без оснований, правообладатель может ответить запросом на удаление.

**Shorts и Content ID.** Раньше Shorts длиннее минуты с активной заявкой блокировались при любой политике правообладателя. Справка о трёхминутных Shorts сообщает: «Starting on September 24, 2026, new Shorts longer than one minute (but under three minutes) with an active Content ID claim will no longer be automatically blocked and may remain playable» ([YouTube Help 15424877](https://support.google.com/youtube/answer/15424877?hl=en)). Справки 6013276, 13053317 и 12504220 пока показывают старую формулировку, то есть страницы противоречат друг другу. Shorts, заблокированные по всему миру, монетизацию не получают. Для Shorts короче минуты ничего не изменилось.

**Allowlist.** Правообладатель со Studio Content Manager может внести канал в allowlist, и тогда «Content ID won't claim any videos uploaded to those channels going forward». Уже выставленные заявки при этом не снимаются. Если у контента несколько владельцев, allowlist должен сделать каждый из них ([YouTube Help 6070344](https://support.google.com/youtube/answer/6070344?hl=en)).

**Likeness detection.** Пилот с CAA начался в декабре 2024 года. С 17–18.05.2026 инструмент доступен любому пользователю 18+ с каналом (нужны паспорт и селфи-видео). 23.09.2026 объявлено, что позже в 2026 году к распознаванию лица добавится распознавание голоса ([PetaPixel](https://petapixel.com/2026/05/18/youtube-will-help-you-find-videos-featuring-your-ai-clone/), [MediaNama](https://www.medianama.com/2026/09/223-youtube-likeness-detection-voices-clones/)). Нарезки и AI-ролики с лицами и голосами реальных людей теперь автоматически попадают этим людям в очередь проверки.

### 2.3 YouTube, уровень 2: монетизация (YPP)

**Reused content** ([YouTube Help 1311392](https://support.google.com/youtube/answer/1311392?hl=en)). Определение: «channels that repurpose content that's already on YouTube or another online source without adding significant original commentary, substantive modifications, or educational or entertainment value». Ключевые фразы для бизнес-модели:
- «This policy applies even if you have permission from the original creator»;
- «Reused content is separate from YouTube's Copyright enforcement, which means it's not based on copyright, permission, or fair use»;
- «Our reused content policy applies to your channel as a whole… monetization may be removed from your entire channel».

| Монетизируется | Не монетизируется |
|---|---|
| Клипы для критического обзора | «Clips of moments from your favorite show edited together with little or no narrative» |
| Реакции с голосовым комментарием к оригиналу | «Short videos you compiled from other social media websites» |
| «Edited footage from other creators where you add a storyline and commentary» | «Content downloaded or copied from another online source without any substantive modifications» |
| Сцена из фильма с переписанными диалогами и новой озвучкой | «Promotion of other people's content (even if you have permission)» |
| Разбор спортивных повторов с объяснением действий | Подборки песен разных исполнителей (даже с разрешением) |
| Ремиксы в Shorts с добавленным собственным контентом | Контент, который уже много раз загружали другие |
| Контент, где в кадре в основном сам автор | Невербальные реакции без голосового комментария; зачитка чужих текстов; песни с изменёнными высотой тона или темпом |

Главный критерий: «we allow reused content if viewers can tell that there's a meaningful difference between the original video and your video». Проверяющие смотрят главную тему канала, самые популярные и самые новые видео, ролики с наибольшей долей времени просмотра, метаданные и раздел About.

**Inauthentic content (15.07.2025).** В журнале изменений: «We're making a minor update to our 'repetitious content' policy». Правило переименовано в «inauthentic content» и явно охватывает «content that is repetitive or mass-produced». (Исправлено: фразы «longstanding guideline» в официальном тексте нет.) Политику reused content это обновление не меняло. Rene Ritchie (YouTube) привёл примеры: «Channels that upload narrative stories with only superficial differences between them», «slideshows that all have the same narration» ([Social Media Today](https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/), [TechCrunch](https://techcrunch.com/2025/07/09/youtube-prepares-crackdown-on-mass-produced-and-repetitive-videos-as-concern-over-ai-slop-grows/)).

**Три категории (июль 2026).** 13–16.07.2026 YouTube разделил неаутентичный контент на три категории. Tubefilter пересказывает позицию YouTube так: «The rules themselves haven't changed» ([Tubefilter, 13.07.2026](https://www.tubefilter.com/2026/07/13/youtube-inauthentic-content-monetization-policy-update/), [TechCrunch, 20.07.2026](https://techcrunch.com/2026/07/20/youtube-clarifies-policies-around-ai-slop-and-upsetting-videos/)):
1. **Generic or Repetitive Content** — «looks like it's made with a template»: слайдшоу, шаблонные сюжеты, бегущий текст без нарратива, «AI-generated content made with generic or unoriginal templates». Допустимы одинаковые интро и аутро, а также серии, где у каждого выпуска «a distinct storyline, focus, or concept».
2. **Unsatisfying or Off-putting Content** — эмоционально-манипулятивные формулы, форматы, «to a degree that the videos feel interchangeable», контент ради шока. (Исправлено.) Пример из текста правил — «videos that stitch together unrelated or inconsistent AI clips to surprise or shock viewers», то есть речь именно о несвязанных ИИ-клипах. Нарезки обычных видео подпадают сюда через общее определение и пример про контент, который «repeatedly uses disturbing themes… without building a cohesive narrative». Фейковые кадры описаны как «realistic visuals tricking viewers into believing a fake celebrity death or natural disaster».
3. **AI Personas Related to Sensitive Topics** — ИИ-персоны, которые выдают себя за экспертов-людей и советуют по здоровью, праву, финансам или политике.

Мэтт Халприн (YouTube) о контент-фермах: «…stuff that's kind of content farming, and that's the stuff that we don't want to have in YPP». Санкции применяются к каналу: при большом объёме такого контента канал исключают из YPP. Практики сообщают, что уже отключённым каналам в июле 2026 года задним числом поменяли причину на новую формулировку.

**Выплаты Shorts** ([YouTube Help 12504220](https://support.google.com/youtube/answer/12504220?hl=en)). Выручка от рекламы между роликами в ленте Shorts раз в месяц собирается в Creator Pool и делится пропорционально engaged views «in each country», то есть отдельно по каждой стране. Автор получает 45% выделенной ему доли. Пример YouTube: доля 1% от пула в $90 000 даёт $900, из них автору $405. В распределении не учитываются:
- «Non-original Shorts, such as unedited clips from others' movies or TV shows, reuploading other creators' content from YouTube or other platform, or compilations with no original content added»;
- накрученные просмотры (боты);
- просмотры контента, не подходящего для рекламодателей.

(Исправлено.) Музыка уменьшает общий пул: при одном треке половина выручки, связанной с роликом, уходит на лицензии, при двух — две трети. Но «Your allocation from the Creator Pool is not affected by your use of a music track»: доля конкретного автора считается от его просмотров. Утверждение «музыка срезает начисление автора вдвое» неверно. Сторонний немузыкальный контент при распределении как вклад не учитывается.

**Пороги YPP** ([YouTube Help 72851](https://support.google.com/youtube/answer/72851?hl=en), [12843009](https://support.google.com/youtube/answer/12843009?hl=en)):

| Уровень | До 31.01.2027 | С 01.02.2027 (для новых каналов) |
|---|---|---|
| Полный (реклама) | 1000 подписчиков + 4000 часов за 12 мес. или 10 млн просмотров Shorts за 90 дней | 1000 подписчиков + 8000 квалифицированных часов за 365 дней или 20 млн квалифицированных просмотров Shorts за 90 дней |
| Доход из пула Shorts | Участие в YPP + Shorts Monetization Module | Дополнительно ≥10 млн квалифицированных просмотров Shorts за скользящие 90 дней |
| Донаты и Shopping | 500 подписчиков + 3 загрузки за 90 дней + 3000 часов или 3 млн Shorts | Без изменений |

Дополнительно:
- канал ниже порога 10 млн остаётся в YPP и зарабатывает на длинных видео;
- новые условия нужно принять в Studio до 31.01.2027;
- при таргетированной рекламе Shorts на 5 каналов или меньше автор получает 45% её выручки напрямую;
- активным считается канал с 1000 часов за год, или 1 млн просмотров Shorts за 90 дней, или 2 длинными видео либо 5 Shorts за 90 дней.

Пороги подтверждены постом блога YouTube от 10.08.2026 (исправлено: не от 13.08). Квалифицированные часы — это только публичные длинные видео, подкасты и архивированные стримы. Часы Shorts в них не засчитываются ([YouTube blog, 12.08.2026](https://blog.youtube/news-and-events/youtube-monetization-qualified-watch-hours-shorts-views/)).

**Процедура.** Решения принимают люди. Заявку проверяют около месяца. Апелляцию можно подать в течение 21 дня. Повторная заявка — через 30 дней, после повторного отказа — через 90. Каналы в программе проверяются постоянно («We continuously check channels in YPP»).

### 2.4 YouTube, уровень 3: Community Guidelines (спам) и удаление каналов

В спам-политике девять категорий: Engagement manipulation, Fake engagement, Comment spam, Off-platform diversion, Detection evasion, Automated or synthetic mass-production, Scraped content, Scams, Malicious clickbait ([YouTube Help 2801973](https://support.google.com/youtube/answer/2801973?hl=en)). Для модели перезалива ключевые три:
- **Detection evasion:** «Technical manipulation (such as speeding up audio, heavy filters, cropping) designed to bypass abuse detection». В примере — телеэпизод, отзеркаленный по горизонтали.
- **Automated or synthetic mass-production:** «Using automated tools or AI to churn out high volumes of similar content with minimal changes». Тесты инструментов и несколько вариантов одного видео допустимы.
- **Scraped content:** «Re-posting material from other websites or platforms, or other videos without adding anything of your own».

(Исправлено.) Off-platform diversion формулируется так: «Content created solely to drive users off of YouTube to external sites, especially those hosting prohibited material». Слово «especially» делает запрещённые материалы главным примером нарушения, но не единственным условием. Вводящие в заблуждение метаданные относятся к категории Malicious clickbait.

**Санкции.** Сначала предупреждение, затем страйки. Три страйка за 90 дней ведут к удалению канала, а «single case of severe abuse (such as… spam…)» — к немедленному удалению ([YouTube Help 2802168](https://support.google.com/youtube/answer/2802168?hl=en)). После удаления: «you are prohibited from using, possessing, or creating any other YouTube channels». Запрет касается существующих, новых и купленных каналов, а также каналов, где владелец «repeatedly or prominently featured». Публикация контента удалённых авторов считается circumvention, и «This may also apply to other channels you own» ([YouTube Help 2801981](https://support.google.com/youtube/answer/2801981?hl=en)). Апелляцию можно подать в течение года.

**Second chance (с 09.10.2025).** Через год после удаления можно попросить новый канал. Пилот не распространяется на каналы, удалённые за нарушение авторских прав или правил Creator Responsibility, а также на тех, кто удалил свой канал или аккаунт сам ([blog.youtube](https://blog.youtube/inside-youtube/second-chances-on-youtube/)).

**Позиция TeamYouTube (13.11.2025).** После разбора сотен жалоб: «The vast majority of termination decisions were upheld». Среди подтверждённых оснований — «mass uploading auto-generated or low-value content; mass uploading content scraped from other creators with minimal edits; content misleading people into clicking off-platform» ([YouTube Community](https://support.google.com/youtube/thread/387431861?hl=en)).

**Масштаб удалений.** За I–III кварталы 2025 года удалено 2 897 659, 2 105 778 и 7 456 811 каналов, всего 12 460 248. Больше 90% приходится на спам и безопасность детей. Всплеск в III квартале YouTube объяснил финансовым мошенничеством из Юго-Восточной Азии ([Dexerto](https://www.dexerto.com/youtube/youtube-responds-to-ai-concerns-as-12-million-channels-terminated-in-2025-3292911/)). Данные за 2026 год извлечь не удалось: страница отчёта рендерится на JavaScript.

**Детекция сетей.** Google Research описал систему S-CTS. Она ищет связанные аккаунты по инфраструктурным признакам («Generation Clusters» с общими инструментами автоматизации или API) и классифицирует шаблонные нарративы при нечеловеческой частоте публикаций. По данным работы, за полгода удалено 50 тыс. кластеров со 130 тыс. каналов, а объём ручной проверки сократился на 50% ([Google Research](https://research.google/pubs/scalable-detection-of-adversarial-synthetic-slop-and-coordinated-media-abuse-a-lora-enabled-multimodal-defense-system/), [SEJ](https://www.searchenginejournal.com/google-generated-ai-detected/579987/)). Вторая работа, SAFE (28.05.2026), описывает многоагентное расследование сетей по связям каналов, синхронным загрузкам и повторяющимся скриптам, но количественных результатов не приводит ([ppc.land](https://ppc.land/googles-safe-uses-4-ai-agents-to-investigate-youtube-slop-networks/)). Это научные публикации, а не отчёт модерации YouTube.

**Приоритет руководства.** Нил Мохан (21.01.2026): «To reduce the spread of low quality AI content, we're actively building on our established systems that have been very successful in combatting spam and clickbait, and reducing the spread of low quality, repetitive content» ([blog.youtube](https://blog.youtube/inside-youtube/the-future-of-youtube-2026/)).

### 2.5 YouTube: раскрытие ИИ и автоматические метки

Раскрывать использование ИИ нужно, если реалистичный контент показывает, как реальный человек говорит или делает то, чего не было, изменяет запись реального события или создаёт реалистичную несуществующую сцену. В список отдельно входит AI-музыка. Раскрытие не нужно для явно нереалистичного контента, фильтров, цветокоррекции, субтитров, клонирования собственного голоса и помощи ИИ при подготовке сценария, названия, превью или инфографики. По заявлению YouTube, «Disclosing AI content won't limit a video's audience or impact its eligibility to earn money». Тем, кто систематически не раскрывает, грозят принудительная метка, удаление контента или отключение от YPP ([YouTube Help 14328491](https://support.google.com/youtube/answer/14328491?hl=en)).

С 27.05.2026 YouTube ставит метки сам — по метаданным C2PA, водяным знакам SynthID и внутренним сигналам. Метку нельзя снять, если ролик сделан инструментами YouTube (Veo, Dream Screen) или если по C2PA он полностью сгенерирован ([The Next Web](https://thenextweb.com/news/youtube-will-now-automatically-label-ai-generated-videos-whether-creators-disclose-them-or-not)). В API раскрытие делается полем `status.containsSyntheticMedia`.

### 2.6 Instagram и Facebook (Meta)

**Правило 2024 года для Reels.** «accounts that repeatedly (10 or more times in the last 30 days) post content from other Instagram users that they didn't create or enhance in a material way will not be shown in surfaces where we recommend content». Из двух и более одинаковых роликов рекомендуется только оригинал, а на копию ставится метка со ссылкой на автора ([creators.instagram.com](https://creators.instagram.com/recommendations-and-originality)).

**Правило 30.04.2026** ([Instagram for Creators](https://creators.instagram.com/blog/rewarding-original-creators-on-instagram)). Аккаунты, которые «primarily post unoriginal content in photos or carousel posts, in addition to reels, will no longer be shown in places where we recommend content». Вернуться в рекомендации можно, «when most of their recently posted photos, carousels, and reels are considered original in a 30-day period. This is calculated on a rolling basis». Скользящее 30-дневное окно подтверждено официально. Подписчикам посты по-прежнему показываются. По данным Instagram, 75% рекомендаций в США уже приходится на оригинальные посты. (Исправлено.) Исключение для паблишеров с лицензиями официально записано только на странице 2024 года; в блоге 2026 года сказано, что правило «separate from our intellectual property policies». Адам Моссери: «If most of what you post to Instagram is someone else's content, your account is no longer going to be recommendable» ([PetaPixel](https://petapixel.com/2026/04/30/new-instagram-policies-target-reposted-content/)).

**Что не считается оригинальным** ([Original Content Guidelines](https://creators.instagram.com/original-content-guidelines)): «low-effort edits (e.g. borders, watermarks, speed changes, just crediting the original creator)». В блоге добавлены субтитры. Тест: «if someone could remove your contribution to your post or reel, and the content would virtually be the same, it probably needs more of you in it». Для курирования чужого без переработки Instagram предлагает Repost, шеринг в Stories, Collab и Remix.

**Recommendations Guidelines и Account Status.** В категорию «low-quality» входит «Unoriginal content that is largely repurposed from another source with only minor, immaterial edits». Если аккаунт не подходит для рекомендаций, «none of your content will be recommended». Кроме того, не рекомендуются аккаунты, которые покупали лайки, получили бан на рекламу или регулярно публикуют фотореалистичного ИИ-человека без метки ([help.instagram.com](https://help.instagram.com/313829416281232/)). Статус виден в Account Status.

**Ранжирование.** Официальные три главных сигнала (Моссери, 22.01.2025): «watch time, likes and sends». Лайки чуть важнее для подписчиков, отправки — для неподписчиков ([Social Media Today](https://www.socialmediatoday.com/news/instagram-shares-algorithm-insights-2025/738034/)). В документе 2023 года указано, что Instagram понижает Reels с водяными знаками, рамками и уже публиковавшиеся ([about.instagram.com](https://about.instagram.com/blog/announcements/instagram-ranking-explained)); документ старый, но новые правила эту линию только ужесточают.

**Непроверенные цифры.** Цифры из маркетинговых блогов — «−60–80% охвата у агрегаторов», «+40–60% у оригинальных авторов», «порог 70% визуального сходства», «sends в 3–5 раз важнее лайков», «55% revenue share на Reels» — в материалах Meta отсутствуют (не подтверждено). Keywords Everywhere прямо пишет: «Neither the 2024 nor the 2026 originality announcement quantifies the effect at all» ([keywordseverywhere.com](https://keywordseverywhere.com/news/instagram-algorithm-updates/)).

**Facebook.**
- 14.07.2025: аккаунты, которые многократно переиспользуют чужие видео, фото или тексты, «will not only lose access to Facebook monetization programs for a period of time, but will also receive reduced distribution on everything they share». Также в посте: «simply stitching together clips or adding your watermark does not qualify as meaningful enhancement». В первой половине 2025 года меры приняты примерно к 500 тыс. аккаунтов за спам, удалено около 10 млн профилей-имперсонаторов. (Исправлено.) Индикатор риска санкций находится на экране Support home, а не в Professional Dashboard ([Facebook for Creators](https://creators.facebook.com/blog/combating-unoriginal-content)).
- 12–13.03.2026: неоригинальным считается «watching along, reacting with facial expressions, stitching multiple clips together, or narrating what's already on screen — without adding anything meaningful», а также рамки, подписи, изменение скорости. При продолжении аккаунт может быть «deemed non-recommendable and demonetized». Просмотры и время просмотра оригинальных Reels во втором полугодии 2025 года примерно удвоились год к году. За 2025 год удалено больше 20 млн имперсонаторов ([About Meta](https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/)).
- 24.04.2025: сетям аккаунтов, которые публикуют один и тот же контент, оставляют показ только подписчикам и отключают монетизацию. В 2024 году удалено больше 100 млн фейковых страниц ([About Meta](https://about.fb.com/news/2025/04/cracking-down-spammy-content-facebook/)).

**Rights Manager** ([Meta Business Help](https://www.facebook.com/business/help/1548693938521733)). Система сопоставляет аудио и видео на Facebook и Instagram. После загрузки эталона за 72 часа выполняется ретроскан, и дальше система «periodically conduct[s] retroactive scans to detect matches from any date in the past». Правила по совпадениям: Block, Monitor, Monetize (доход с Facebook Reels пропорционально совпадающему фрагменту и только на территориях прав), ручная проверка, запрос на удаление, allow-list. Допуск получает только владелец исключительных прав. Один из стоп-вопросов при допуске — «compilations of clips from other sources». Для отдельных авторов Rights Manager «is going away soon» и переезжает в content protection.

**Content protection (17.11.2025)** ([Facebook for Creators](https://creators.facebook.com/blog/content-protection-your-new-creative-guardian-on-facebook)). Непрерывно сканирует Facebook и Instagram на полные и частичные совпадения с защищёнными Reels, опубликованными в Facebook (кросспост из Instagram через «Share to Facebook» тоже засчитывается). Действия: Track (по умолчанию, можно добавить ссылку «original by»), Block (копия пропадает на FB и IG), Release. (Исправлено.) Сканирование автоматическое, но блокировку автор включает вручную по каждому совпадению, и доступ есть только у допущенных авторов (участники Content Monetization с «enhanced integrity and originality standards», пользователи Rights Manager, остальные по заявке), а не у «любого крупного автора».

**Повторные нарушения.** Валидные жалобы Meta обрабатывает «typically within a day or less» ([Meta Transparency](https://transparency.meta.com/reports/intellectual-property/protecting-intellectual-property-rights/)). Повторным нарушителям отключают аккаунт. Числовой порог страйков Meta не публикует.

**Указание автора ничего не меняет.** Справка Instagram перечисляет, что нарушение возможно, «even if you»: «Gave credit to the copyright owner», «Modified the work or added your own original material to it», «Found the content available on the internet», «Saw that others posted the same content as well», «Think that the use is a fair use» ([help.instagram.com](https://help.instagram.com/354736791367645/)).

**Монетизация Meta.** «Content that is unoriginal or reproduced without making meaningful enhancements (commentary, parody, creative editing, etc.) cannot be monetized». Запрещены статичные видео, слайдшоу, зацикленные видео, текстовые монтажи, встроенная реклама. Пункт «Avoid systemic violations» позволяет лишить монетизации, если связанная сущность неоднократно нарушала правила ([help.instagram.com](https://help.instagram.com/2635536099905516)).

**Ложные срабатывания.** Oversight Board (04.06.2026) отметил в управлении аккаунтами Meta «lack of transparency and consistency» и неэффективность апелляций, которые прогоняют через тот же автоматический процесс ([TechCrunch](https://techcrunch.com/2026/06/04/metas-oversight-board-says-account-bans-lack-due-process-transparency/)). Паблишер Interesting Engineering (16 млн подписчиков) был демонетизирован за «unoriginal»; флаги ставились задним числом по архиву ([Interesting Engineering](https://interestingengineering.com/culture/meta-facebook-enforcement-system-monetization)).

### 2.7 TikTok (для полноты)

В Creator Rewards не засчитываются «Duet or Stitch videos», «Content reproduced from others with only slight modifications, including videos that are sped up or contain filters, fixed texts, or stickers». Условия участия: 10 000 подписчиков, 100 000 просмотров за 30 дней, видео от минуты, личный (не бизнес) аккаунт ([TikTok Support](https://www.tiktok.com/support/faq_detail?id=7581821550694013452)).

### 2.8 Сводная таблица санкций

| Механизм | Что его запускает | Последствие | Масштаб | Обжалование |
|---|---|---|---|---|
| Content ID (YouTube) | Совпадение с эталоном | Блок, доход правообладателю или отслеживание | Ролик | Спор; при необоснованном споре правообладатель может ответить страйком |
| Страйк за авторское право | Запрос правообладателя на удаление | Удаление ролика; 3 страйка за 90 дней — удаление аккаунта и связанных каналов | Аккаунт | Встречное уведомление, отзыв жалобы |
| Reused / inauthentic (YPP) | Проверка канала людьми | Отказ или исключение из YPP | Весь канал плюс «related channels» | Апелляция 21 день; повторная заявка через 30 или 90 дней |
| Спам-политика (CG) | Detection evasion, scraped, mass-production | Страйк; при серьёзном случае сразу удаление | Все каналы владельца | Одна апелляция в течение года |
| Инструменты Meta (Rights Manager, content protection) | Полное или частичное совпадение, ретроскан | Блок, монетизация в пользу правообладателя, удаление | Пост; при повторах — аккаунт | Спор внутри системы |
| Оригинальность Instagram | Большинство неоригинальных постов за 30 дней | Аккаунт убирают из рекомендаций | Весь аккаунт | Account Status, удаление постов |
| Неоригинальность Facebook | Повтор чужого контента | Демонетизация, урезание охвата всех постов | Аккаунт | Апелляция |

---

## 3. Почему «уникализация» не работает как бизнес-модель

### 3.1 Приёмы «уникализации» прямо описаны в правилах как нарушение

Типовые приёмы — ускорение аудио, фильтры, кроп, зеркалирование — YouTube перечисляет в спам-политике как «detection evasion». Это нарушение Community Guidelines со страйками вплоть до удаления, а не просто повод отказать в монетизации. Instagram официально относит рамки, водяные знаки, субтитры, смену скорости и подпись с автором к «low-effort edits». Facebook — склейку клипов, пересказ происходящего на экране, реакции мимикой, рамки и изменение скорости. TikTok — ускорение, фильтры, постоянный текст и стикеры. У всех трёх площадок тест один: если убрать вклад загрузившего, останется ли тот же ролик.

Отсюда первый факт для бизнес-модели: всё, что делает конвейер «уникализации», по формулировкам правил признак нарушения, а не защита от него.

### 3.2 Детекция накапливается во времени

- Content ID сверяет каждую загрузку с эталонами, и правообладатели постоянно добавляют новые.
- Rights Manager Meta периодически сканирует задним числом «from any date in the past», content protection ищет частичные совпадения.
- Copyright Match Tool (173 тыс. пользователей в 2025 году) и likeness detection (с мая 2026 года доступен всем 18+) передают поиск копий самим авторам.
- Сетевая детекция (S-CTS, SAFE) смотрит не на отдельный ролик, а на кластер аккаунтов с общей автоматикой.

Ролик, который прошёл сегодня, может получить заявку или блокировку через месяцы. Для Instagram практики фиксируют, что флаги «unoriginal» ставятся ретроспективно по архиву.

### 3.3 Экономика: даже когда ролик остался в доступе

1. **Заявка Content ID с политикой monetize** отдаёт рекламу правообладателю. Так поступают больше чем с 90% заявок.
2. **Неоригинальные Shorts исключены из Creator Pool.** Даже в YPP такие просмотры не приносят денег.
3. **Правило reused действует на весь канал.** Несколько нарушающих роликов демонетизируют и оригинальные.
4. **С 01.02.2027 каждый канал должен сам держать ≥10 млн квалифицированных просмотров Shorts за 90 дней.** Сеть мелких каналов по 1–3 млн просмотров в месяц дохода из пула не получит в принципе.
5. **У Instagram нет рекламной доли за Reels**, так что на уровне площадки здесь нечего терять и нечего получать.
6. **Для РФ YPP закрыт вовсе.** Остаются продакт-плейсмент и воронки, а у них свои правовые ограничения (раздел 9).

**Прикидка по сети (оценка).** Возьмём 10 Shorts-каналов по 10 млн публичных просмотров в месяц, всего 100 млн просмотров. Если бы контент был оригинальным и канал прошёл в YPP, при $10–50 за 1 млн публичных просмотров это дало бы $1000–5000 в месяц. На перезаливах доход из YPP равен нулю: просмотры неоригинальных Shorts не считаются, заявленный контент монетизирует правообладатель. Потенциальный убыток при этом — вся сеть, включая «чистые» каналы того же владельца.

### 3.4 Риск для всей сети

- **YouTube:** удаление запрещает все каналы владельца (circumvention). Исключение из YPP распространяется на «related channels». Три страйка на связанном канале могут удалить ваш.
- **Практика РФ:** когда один канал на общем «посредническом» AdSense получал три страйка, YouTube удалял все каналы на этом AdSense. Письмо YouTube: «Мы удалили ваш канал, поскольку пришли к выводу, что он связан с другим каналом, который также был удален за наличие трех или более предупреждений о нарушении авторских прав» ([searchengines.guru](https://searchengines.guru/ru/forum/1133987), 08.11.2025; [1115177](https://searchengines.guru/ru/forum/1115177), 01.01.2025).
- **Instagram:** практики сообщают, что каждый новый аккаунт, связанный с заблокированным, тоже блокируют, иногда за 6–24 часа ([Reddit, 09.2026](https://www.reddit.com/r/Instagram/comments/1wkbs1u/instagram_keeps_suspending_every_account_i_create/)). Условия использования запрещают покупку и продажу аккаунтов, а также автоматизированный доступ без разрешения.

### 3.5 Что сообщают практики (англоязычные источники)

| Дата | Кейс | Итог |
|---|---|---|
| 2025 (начало) → 18.12.2025 | Screen Culture и KH Studio: фейковые ИИ-трейлеры на основе чужих промоматериалов, 2 млн+ подписчиков, 1 млрд+ просмотров | После демонетизации в названия добавили «fan trailer» и «parody» и вернулись в YPP. Удалены 18.12.2025: «once monetizing again, they reverted to clear violations of our spam and misleading metadata policies» ([TheWrap](https://www.thewrap.com/industry-news/tech/youtube-terminates-screen-culture-kh-studio-over-ai-fake-movie-trailers/)) |
| 29.01.2026 | Топ-100 AI-slop по Kapwing | 16 каналов удалены или очищены: 35 млн подписчиков, 4,7 млрд просмотров, ~$10 млн в год (оценка). Cuentos Facinantes (5,95 млн подписчиков) удалён ([XDA](https://www.xda-developers.com/youtube-just-deleted-over-4-7-billion-views-worth-ofai-slop-videos/), [Tubefilter](https://www.tubefilter.com/2026/01/29/youtube-ai-slop-channel-crackdown-bans/)) |
| 04.08.2026 | Сеть игровых каналов, 1 млн+ подписчиков в сумме | Всё удалено за Spam/deceptive. Часть каналов восстановили после апелляции и меньше чем через час удалили снова ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1vf67ju/youtube_terminated_my_channels_1m_subscribers/)) |
| 19.04.2026 | Ремикс-канал на 420 тыс., верифицированный | Удалён за «spam / scam / deceptive practices» ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1spn0gn/420k_subscribersverified_channel_taken_down_for/)) |
| 08.06.2026 | Reqx, 113 тыс. подписчиков, 13 млн просмотров | Получил отказ за reused, сам удалил 8–10 Shorts, после чего канал удалили за спам ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1u0oamg/reqx_channel_113k_terminated_for_spam_false/)) |
| 18.08.2026 | Три монетизированных канала на одном аккаунте | Один исключён за reused, после окончания срока апелляции остальные исключены за «Related channel». Через 3 месяца два одобрены, ещё один снова отклонён за reused ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1vrotg1/does_reapplication_rejection_also_trigger_related/)) |
| 11.07.2026 | 24-часовая компиляция стримера с его согласия | Удалён контент на 200 тыс. просмотров, 3+ повторные заявки — отказ ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1ut7agu/urgent_advice_for_partner_appeal/)) |
| 27.07.2026 | Видеоапелляция: 5 минут объяснений и таймлапс монтажа | Отказ: «Appeal didn't demonstrate enough value-add editing… Taking someone else's content and making minimal changes is still violative» ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1v821fk/appeal_rejected_for_reused_content_need_help/)) |
| 12.08.2026 | Канал клипов стримеров (Shorts) | Владелец сам пришёл к выводу, что формат целиком подпадает под reused и апелляцию не пройти ([Reddit](https://www.reddit.com/r/NewTubers/comments/1vmrqsr/where_to_go_from_here_faceless_streamer_clip/)) |
| 22.08.2026 | Канал клипов VTuber'ов, 3 Shorts в день | За 25 дней просмотры упали с 800–1000 до <20 на ролик, страйков нет ([Reddit](https://www.reddit.com/r/NewTubers/comments/1vuxttt/i_have_clip_channel_that_just_landed_in_0_view/)) |
| 30.06.2026 | Авиационные Shorts на чужих кадрах с текстовыми комментариями | Повторное отключение за reused, в прошлый раз ждали 90 дней ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1ujudt8/has_anyone_successfully_appealed_reused_content/)) |
| 18.10.2023 | Instagram-аккаунт на 896 тыс., чужой контент | Отключён за авторские права. В той же ветке похожие случаи на 180 тыс. – 1,3 млн подписчиков ([BlackHatWorld](https://www.blackhatworld.com/seo/i-got-an-896k-instagram-account-banned-for-copyright-any-advises.1538163/); данные до 2025 года) |
| 06.09.2026 | Instagram: шаблоны для green screen, эдиты | Флаг «unoriginal» трижды, пришлось удалить 60+ видео; рекомендаций лишается весь аккаунт ([Reddit](https://www.reddit.com/r/Instagram/comments/1w8s0lm/has_anyone_has_issued_with_new_instagrams/)) |
| 20.09.2026 | Instagram: медиа-страница после расширения правила | «my reach has taken a big hit… now it has stalled» ([Reddit](https://www.reddit.com/r/InstagramMarketing/comments/1wkt6kh/instagram_is_now_quietly_penalizing_accounts_that/)) |
| 17.08.2026 | Facebook-страница о кино на 28 тыс. | Потеряла монетизацию ([Reddit](https://www.reddit.com/r/SocialMediaMarketing/comments/1vr0x5o/posting_same_clip_on_two_different_facebook_pages/)) |

Санкции задевают и оригинальных авторов. Тред на форуме YouTube о канале с концепт-карами (30.01.2026) собрал 123 отметки «I have the same question». Канал с лором Cyberpunk и Fallout (300 млн+ просмотров Shorts, свой голос, лицо и около 3 ТБ собственного b-roll) получил ярлык inauthentic. Каналу на 250 тыс. отказали в апелляции, каналу почти на 600 тыс. отказали после 16 дней повторной проверки через partner manager ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1sdvbo5/any_luck_dealing_with_inauthentic_content_notice/)). Версию о том, что причина в публикации одних и тех же видео и как длинных роликов, и как Shorts, выдвинул комментатор, а не автор поста (исправлено).

### 3.6 Что сообщают практики (русскоязычные источники)

| Дата | Кейс | Итог |
|---|---|---|
| 26.08.2024 | Нарезки из фильмов с переходами, цветокоррекцией, эффектами и музыкой без авторских прав, 3 месяца монетизации | Монетизацию отключили; в апелляции ответ «ваш контент почти не отличается от оригинала», повторная подача через 90 дней ([searchengines.guru](https://searchengines.guru/ru/forum/1103844)) |
| 22.08.2024 | Авторский канал (с 2016 года) перезалил ролик из TikTok в Shorts, 200 тыс. просмотров | Жалоба автора с немедленным удалением, отключение монетизации всего канала ([searchengines.guru](https://searchengines.guru/ru/forum/1103619)) |
| 20.03.2026 | Текстовые шортсы по 6–10 секунд на стоковых фонах | Монетизацию сняли через неделю после подключения; участники форума предупреждают, что видеоапелляция может задеть другие каналы ([searchengines.guru](https://searchengines.guru/ru/forum/1139696)) |
| 30.03.2026 | Канал «сток + TTS» | Отказ по «repetitive content» ([vc.ru](https://vc.ru/2840314)) |
| 30.03.2026 | Анимационный Shorts-канал, 70 млн просмотров за 48 часов, $1200–1500 в день | Отключён за «опасный контент», апелляция отклонена, потери около $25 тыс. ([vc.ru](https://vc.ru/2840314)) |
| 14.08.2025 | Пересказ WSJ: пользователь Reddit нарезал чужие видео, добавил аудио и кадры, 15 млн просмотров | Страйк за повторное использование контента ([vc.ru](https://vc.ru/2157481)) |
| 30.05.2026 | Арбитражники TikTok, Reels и Shorts | На неуникальных роликах «0 просмотров», теневые ограничения ([vc.ru](https://vc.ru/2955100)) |
| 2025 | Посреднический AdSense | Каскадное удаление всех каналов на аккаунте (см. 3.4) |

### 3.7 Ошибка выжившего

- Kapwing в ноябре 2025 года оценивал топ AI-slop-каналов в миллионы долларов в год, а к январю 2026 года часть из них была удалена. Проверка 26.09.2026 по хэндлам: по адресу @CuentosFacinantes открывается новый канал с двумя подписчиками, @ScreenCulture помечен «Handle for sale», @KHStudio отдаёт 404.
- Кейс «45 000 ₽ в месяц на перезаливах в Shorts и TikTok» (vc.ru, 05.07.2026) — это самоотчёт без данных о санкциях, а доход там идёт от рекламы в Telegram и партнёрок, включая гемблинг ([vc.ru](https://vc.ru/3012010)).
- Большая часть «доходных» постов о клиппинге на r/passive_income содержит реферальные ссылки или рекламу софта. Один из скептиков пишет: «Most people are selling a dream».

### 3.8 Итог раздела

Модель «скачать, изменить, перезалить» зарабатывает, пока канал жив. Выручка идёт не через рекламную программу площадки, а через продакт-плейсмент, партнёрки и воронки в сторонние каналы. Продолжительность жизни канала определяют автоматические системы, которые за 2024–2026 годы стали строже: работают ретроскан, частичные совпадения, детекция сетей, правила на уровне всего аккаунта и санкции против связанных каналов. Заявленный контент приносит деньги правообладателю. Для оператора из РФ рекламная выручка YouTube недоступна при любом контенте. Рентабельный конвейер строится на источниках из раздела 4.

---

## 4. Легальные источники большого объёма контента и правовая база

### 4.1 Клиппинг-программы (контент правообладателя, оплата за просмотры)

**Как устроено** ([документация Whop Content Rewards](https://docs.whop.com/memberships-and-access/third-party-apps/content-rewards.md)). Бренд, стример или подкаст запускает кампанию типа Clipping или UGC. Он задаёт ставку за 1000 просмотров, бюджет, минимальную и максимальную выплату за ролик, платформы (TikTok, YouTube Shorts, X, Instagram Reels) и выдаёт исходники: «Access to your video footage and any assets they can use». Платят только за ролики, которые одобрил заказчик. Пример из документации: при $3 за 1000 и минимуме $6 в проверку попадают ролики от 2000 просмотров, а при потолке $3000 начисление останавливается на 1 млн просмотров. Право на использование исходников выдаёт тот, кто запускает кампанию, в пределах брифа. Это спонсорский контент, поэтому на YouTube его нужно отмечать как paid promotion.

**Ставки и масштаб**

| Площадка | Ставка | Масштаб, условия | Источник |
|---|---|---|---|
| Whop Content Rewards | В среднем ~$1 за 1000 (блог Whop); Whop Clips — $1,25 за 1000 для YouTube | Бюджет Whop Clips $190 000/мес; стример Neon заплатил клипперам больше $300 000 (данные вендора) | [Whop blog](https://whop.com/blog/content-rewards/), [Whop Clips](https://whop.com/blog/whop-clips/) |
| Кампания Lovable (05.2025) | $2 за 1000 | Бюджет $10 000, только зрители из US/UK/CA/AU/NZ | [Whop](https://whop.com/blog/content-rewards-x-lovable/) |
| Гайды 2026 | $0,20–6 за 1000, в среднем ~$1 | Roobet — $1,50 за 1000 при бюджете $250K (исправлено: не $1). Кампании Call of Duty MW4 ($1,5 за 1000, $120K) и Boxabl ($0,5 за 1000, $85K) — не подтверждено | [OpenClip](https://openclip.app/guides/whop-clipping-guide) |
| NPR (12.05.2026) | $1 за 1000 (клипы MLB); $25 за 1000 (ИИ-стартап); Polymarket $0,50 за 1000 | Бюджет Polymarket $70 000 | [NPR](https://www.npr.org/2026/05/12/nx-s1-5794670/influencers-creators-video-clips) |
| Clipping.net | $40–300 за 100K ($0,4–3 за 1000) — это снимок текущих кампаний, а не ставка платформы | $60M+ выплат, 77 000+ клипперов; порог оплаты от 10K до 500K просмотров; выплаты PayPal, USDT/USDC, BTC, ETH; за накрутку — «Forfeiture of all pending and earned Payouts» | [clipping.net](https://clipping.net), [условия](https://clipping.net/policies/clipper-terms-and-conditions) |
| Vyro | $1000–1500 за 1 млн | Вывод от $10 раз в неделю через Stripe или PayPal; кампании MrBeast и Mark Rober | [vyro.com](https://vyro.com/) |
| Content Rewards | Ставку задаёт бренд | Комиссия платформы 10%, кампании от $1000; выплаты только через Whop | [contentrewards.com](https://www.contentrewards.com/terms) |
| Klipni (РФ) | CPM задаёт бренд | Кабинет бренда от 4 900 ₽/мес; ОРД, erid и документы для самозанятых сервис делает сам; 12 713 креаторов, 56 кампаний (данные вендора) | [klipni.com](https://klipni.com/) |
| Стримеры СНГ | Прайс стримера «Злой»: $17,5 за 100K, ~$140 за 1 млн (исправлено: это прайс одного стримера, а не рынка) | Выплаты в USDT; ставки меняются без предупреждения; ~90% новичков бросают в первые 3 недели; успешный новичок — ~$1700 к концу первого месяца | [vc.ru](https://vc.ru/2918998) |
| Кейс-сайты CS2 (Musor Drop, Upgrader) | TikTok/Reels $1,5 за 1000 (CS2), $0,3 (прочее); Shorts $0,75 и $0,15 | Платят за вставку своих оверлеев; это околгемблинговая реклама | [vc.ru, 31.08.2026](https://vc.ru/3108788) |
| Контент-заводы UGC (РФ) | CPV 0,1 ₽ | Тендер: 1000 единиц контента, договор на 3 месяца | [vc.ru, 10.07.2026](https://vc.ru/3017932) |

**Что получают практики.**
- 30 дней кампаний с логотипом бренда: $2372 за 22 млн просмотров, то есть ~$0,11 за 1000 ([Reddit, 18.05.2026](https://www.reddit.com/r/passive_income/comments/1tgljtu/hit_2372_in_my_first_month_of_clipping_is_this/)).
- Менеджер кампании: 1,48 млн просмотров, клипперам выплачено $2800, себе около $900 комиссии ([Reddit](https://www.reddit.com/r/passive_income/comments/1tf0ns1/i_made_900_managing_a_clipping_campaign_that_did/)).
- Клиппер из Индии: 17 роликов за 2 месяца, выведено $27, дальше отказ в оплате из-за региона ([Reddit](https://www.reddit.com/r/NewTubers/comments/1v74k3i/whos_actually_making_money_clipping_from_india/)).
- Клиппер из РФ: фиксированная оплата около $120 в месяц в криптовалюте ([vc.ru, 09.09.2026](https://vc.ru/3128413)).

«Заморозка выплат при подозрении на ботов» и «споры до 90 дней» в проверенных источниках не найдены (не подтверждено). Подтверждено другое: ролики отклоняют уже после того, как они набрали просмотры; бюджеты кампаний заканчиваются; расхождения между трекером и аналитикой площадки решаются в пользу кампании.

**Доступ из РФ.** Whop прямо отказывает в проверке личности людям в России («doesn't offer identity verification to people in these countries», RU в списке) ([Whop docs](https://docs.whop.com/developer/verification/identity-documents.md)). Content Rewards выплачивает только через Whop и проверяет пользователей по санкционным спискам. Vyro платит через Stripe или PayPal, а ни тот ни другой Россию не обслуживает. У Clipping.net из доступных способов остаётся криптовалюта, а с 01.09.2026 у неё новый режим по 173-ФЗ (см. раздел 9). В РФ законно работают Klipni, прямые договоры со стримерами и брендами и UGC-тендеры.

**Главное ограничение.** Деньги в клиппинге платит заказчик кампании, а не YouTube. Для YPP правило reused content продолжает действовать: «Promotion of other people's content (even if you have permission)» не монетизируется. Отчётов об успешной рекламной монетизации YouTube у каналов из лицензированных клипов не найдено.

### 4.2 Лицензии и распределение дохода

**Собственный архив правообладателя (самый чистый вариант).** YouTube даёт владельцу видео встроенные инструменты:
- «Edit into a Short» — фрагмент до 60 секунд со ссылкой на оригинал, «only you can edit your long-form video into a Short». Не работает для приватных и скрытых видео и для видео с заявками третьих лиц ([YouTube Help 12836917](https://support.google.com/youtube/answer/12836917)).
- «Turn audio into Short» для аудиоподкастов — «an unlimited number of Shorts between 8 seconds and 3 minutes». Только английский язык, только десктоп, только в AU, CA, UK и US ([YouTube Help 15260303](https://support.google.com/youtube/answer/15260303)).
- Ссылка «Related video» из Short на длинное видео.

Крупные подкасты держат собственные официальные клип-каналы, например Lex Clips у Lex Fridman ([lexfridman.com](https://lexfridman.com/podcast/)). Кейс вендора OpusClip: тематический канал на нарезках из собственного архива в 800+ эпизодов вышел на монетизацию за 40 дней и получил $6563 рекламного дохода за первые 60 дней ([OpusClip](https://www.opus.pro/blog/from-zero-to-monetization-for-a-new-youtube-channel-in-40-days); данные вендора, не подтверждено).

**Раздел дохода.** В Content ID нет встроенного механизма, который делил бы доход с загрузившим, если контент не музыкальный. При политике monetize все деньги получает правообладатель. Делить выручку с издателем можно только при заявке на музыку ([YouTube Help 6013276](https://support.google.com/youtube/answer/6013276?hl=en)). Поэтому для немузыкального контента раздел дохода возможен только по частному договору. Варианты:
- реклама идёт на AdSense правообладателя, студия нарезки получает фикс или процент;
- канал студии получает эксклюзивную лицензию и оформлен в MCN как Owned & Operated, то есть «the partner has obtained exclusive rights to the channel's YouTube content» ([YouTube Help 2737059](https://support.google.com/youtube/answer/2737059)). При этом каждый канал MCN всё равно проходит проверку YPP.

Публичных первичных документов с процентами (например, 50/50) не найдено (не подтверждено).

**Лицензии на вирусное видео.**

| Поставщик | Цена | Условия |
|---|---|---|
| [Newsflare](https://www.newsflare.com/pricing) | Growing Publishers (до 3 платформ): $230 за ролик, $2200 за 10. Established (4+): $355 и $3200. Production (ТВ, стриминг): $695 на 1–5 лет, $950 на 5+ лет | Весь мир, одна публикация на каждом канале, выпуск в течение 30 дней. «Videos cannot be used for advertising or promotional use» |
| [ViralHog](https://viralhog.com/) | По запросу, скидки за объём | Эксклюзивный представитель; монетизирует ролики на YouTube сам |
| [Storyful](https://storyful.com/) | Подписка для редакций | Лицензия «for web, apps, broadcast, and social» |

Экономика (оценка): при $230 за ролик и RPM Shorts в центах лицензия окупается только в длинных видео с высоким RPM или при редакционной модели.

**Twitch и Kick.** Если стример включил соответствующую настройку, зрители могут публиковать его клипы (до 60 секунд) в свои YouTube, TikTok и Instagram. Лицензия по ToS Twitch действует «solely as necessary to enable such functionality» ([Twitch ToS, 12.08.2026](https://legal.twitch.com/en/legal/terms-of-service/), [Twitch Help](https://help.twitch.tv/s/article/how-to-use-clips?language=en_US)). Включённая настройка подтверждает согласие стримера на шеринг, но договором о монетизации не является. У Kick отдельного пункта о клипах нет ([Kick ToS, 23.03.2026](https://kick.com/terms-of-service)).

**Instagram Collab.** Пост показывается в двух профилях и подписчикам обоих авторов, но «The original author owns the post»: если автор его удалит, пост исчезнет и у соавтора ([Instagram Help](https://help.instagram.com/291200585956732)). Для Meta это рекомендованный способ публиковать совместно с автором.

**Facebook.** Content protection позволяет правообладателю оставить копию партнёра, пометив её «original by» (Track). Creator Fast Track (18.03.2026) платит авторам, которые переносят свой контент с других площадок: $1000 в месяц при 100 тыс.+ подписчиков и $3000 в месяц при 1 млн+, в течение трёх месяцев. Программа доступна не во всех странах ([About Meta](https://about.fb.com/news/2026/03/creator-fast-track-grow-your-audience-earn-money-on-facebook/)).

### 4.3 Creative Commons (CC BY)

- **Где искать:** фильтр «Creative Commons» в поиске YouTube, в API — `search.list` с `videoLicense=creativeCommon` (по умолчанию 100 вызовов в сутки, отдельная корзина квоты) ([YouTube Help 2797468](https://support.google.com/youtube/answer/2797468?hl=en), [API](https://developers.google.com/youtube/v3/docs/search/list)).
- **Атрибуция:** название, автор, источник (URL), лицензия; можно текстом на экране или голосом. Монетизация разрешена, если лицензия допускает коммерческое использование. CC BY 3.0 бессрочна на весь срок охраны, изменения нужно помечать ([legal code](https://creativecommons.org/licenses/by/3.0/legalcode)).
- **Ограничения:** правило reused content действует и здесь, поэтому перезалив CC-ролика без переработки не монетизируется. CC-метку мог поставить не правообладатель, и тогда она никаких прав не даёт. На ролик с заявкой Content ID лицензию CC BY поставить нельзя.

### 4.4 Общественное достояние

- **NASA:** материалы «generally are not subject to copyright in the United States». Нельзя создавать впечатление одобрения NASA; логотип и эмблема не входят в общественное достояние; образы действующих астронавтов в коммерции запрещены ([nasa.gov](https://www.nasa.gov/nasa-brand-center/images-and-media/)).
- **Prelinger Archives / Internet Archive:** «Many people believe that all Prelinger Archives films are in the public domain. Some are. Many are not». Свободно можно использовать только файлы с пометкой CC Public Domain Dedication. Внутри фильмов может быть защищённая музыка. Internet Archive прав не выдаёт и статус не гарантирует ([archive.org](https://archive.org/details/prelinger), [help](https://help.archive.org/help/rights/)).

### 4.5 Стоки

| Сток | Можно | Нельзя | API |
|---|---|---|---|
| [Pexels](https://www.pexels.com/license/) | Коммерческое использование и изменения, атрибуция не обязательна | Продавать неизменённые копии, выкладывать на другие стоки, изображать одобрение брендом или человеком | 200 запросов в час, 20 000 в месяц; ссылка на Pexels обязательна |
| [Pixabay](https://pixabay.com/service/license-summary/) | Коммерческое использование с переработкой | Распространять «on a Standalone basis», использовать узнаваемые бренды | 100 запросов за 60 с, кеш 24 ч, «Systematic mass downloads are not allowed»; часть музыки зарегистрирована в Content ID (спор решается ссылкой на лицензию) |
| [Mixkit](https://mixkit.co/license/) | Видео прямо разрешены для «YouTube videos, Social Media video posts», атрибуция не нужна | Музыку нельзя регистрировать в системах управления правами; нельзя собирать материалы и выдавать их «on a stock or inventory basis» (условия от 02.10.2025) | Публичного API не найдено |
| [Coverr](https://coverr.co/license) | Коммерческое использование бесплатно | Перепродажа, конкурирующие сервисы, обучение ИИ | Есть |
| Videvo → Freepik → [Magnific](https://www.magnific.com/legal/terms-of-use) | Бесплатное использование с атрибуцией | Без атрибуции — только по Premium, Premium+ или Pro | — |

(Исправлено.) У Magnific нет общего требования Premium для коммерческого использования: Premium нужен, чтобы использовать контент без атрибуции.

Стоки работают как фон (B-roll). Под inauthentic content подпадает шаблонный контент без нарратива и комментария, а не само по себе использование стоков. Одни и те же популярные футажи при этом стоят в тысячах генераторных каналов.

### 4.6 Музыка

- **YouTube Audio Library:** «If you're in the YouTube Partner Program, you can monetize videos with music and sound effects from the Audio Library». Для CC-треков нужна атрибуция ([YouTube Help 3376882](https://support.google.com/youtube/answer/3376882?hl=en)).
- **Meta Sound Collection:** «over 14,000 songs and sounds which are entirely royalty free», коммерческое использование и реклама разрешены. Основная музыкальная библиотека Instagram предназначена «for personal, non-commercial use», и части бизнес-аккаунтов она недоступна ([Instagram Help](https://help.instagram.com/402084904469945)). С 01.06.2026 через Instagram Audio API можно искать оригинальные звуки и royalty-free музыку из Sound Collection.
- **Epidemic Sound** (цены при оплате за год): Creator — €10,99/мес, монетизация «1 channel per platform»; Pro — €16,99/мес, 3 канала на платформу; Pro Plus — €39,99/мес ([epidemicsound.com](https://www.epidemicsound.com/pricing/)). Можно ли оплатить из РФ, не проверялось.
- **Shorts:** трек уменьшает общий Creator Pool, но не долю конкретного автора (см. 2.3).

### 4.7 Правовая база

**США.**
- Fair use определяется судом по четырём факторам 17 U.S.C. §107: цель и характер использования, природа произведения, объём взятого, влияние на рынок. «there is no formula to ensure that a predetermined percentage or amount of a work... may be used without permission» ([Copyright Office](https://www.copyright.gov/fair-use/)).
- YouTube: «Giving credit to the owner of a copyrighted work won't by itself turn a non-transformative copy of their material into fair use»; «Automated systems like Content ID can't decide fair use» ([YouTube Help 9783148](https://support.google.com/youtube/answer/9783148?hl=en)).
- Warhol v. Goldsmith (18.05.2023, решение 7–2): если вторичное использование служит той же коммерческой цели, небольших изменений для трансформативности мало.
- Hosseinzadeh v. Klein (S.D.N.Y., 23.08.2017): 14-минутная реакция с ~3 минутами чужого скетча признана fair use как «quintessential criticism and comment» ([Copyright Office](https://www.copyright.gov/fair-use/summaries/hosseinzadeh-klein-sdny2017.pdf)).
- Ted Entertainment v. Saber (Denims): 05.06.2026 стрим-реакция признана fair use (по Википедии; суд и исход апелляции не подтверждены). Equals Three v. Jukin Media (2015): часть эпизодов признана трансформативной, часть нет (по памяти исследователя, не подтверждено).
- DMCA §512(g): после встречного уведомления ролик восстанавливают через 10–14 рабочих дней, если заявитель не подал иск. Встречное уведомление содержит согласие на юрисдикцию федерального суда США и раскрывает имя и адрес ([17 U.S.C. §512](https://www.law.cornell.edu/uscode/text/17/512)).
- Установленная законом компенсация по §504(c): $750–30 000 за произведение, до $150 000 при умышленном нарушении (в исследовании не сверено с первоисточником).
- ИИ-контент: «Copyright does not extend to purely AI-generated material», и «prompts do not alone provide sufficient control» (отчёт Copyright Office от 29.01.2025). Апелляционный суд округа Колумбия подтвердил требование человеческого авторства (Thaler v. Perlmutter, 18.03.2025) ([USCO](https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf), [решение](https://media.cadc.uscourts.gov/opinions/docs/2025/03/23-5233.pdf)). Полностью сгенерированные ролики конкуренты могут копировать свободно.

**ЕС.** Ст. 17 Директивы DSM 2019/790 делает площадки ответственными за контент пользователей и требует notice-and-staydown. Исключения для цитаты, критики, пародии и пастиша работают через обжалование. Суд ЕС 26.04.2022 (C-401/19) иск Польши отклонил ([EUR-Lex](https://eur-lex.europa.eu/eli/dir/2019/790/oj/eng)).

**РФ.**
- **Ст. 1270 ГК:** воспроизведение (подп. 1), переработка (подп. 9) и доведение до всеобщего сведения (подп. 11) требуют согласия правообладателя ([zakonrf](https://www.zakonrf.info/gk/1270/)).
- **Ст. 1274 ГК:** цитирование без согласия допускается «в научных, полемических, критических, информационных, учебных целях… в объеме, оправданном целью цитирования» с указанием автора и источника. Пародия и карикатура тоже разрешены. Развлекательной цели и цели извлечения прибыли в перечне нет ([Консультант](https://www.consultant.ru/document/cons_doc_LAW_64629/84bbd636598a59112a4fe972432343dd4f51da1d/)).
- **Ст. 1301 ГК (214-ФЗ от 07.07.2025, в силе с ~04.01.2026):** компенсация от 10 тыс. до 10 млн ₽ (раньше до 5 млн) либо двукратная стоимость экземпляров или права. Новая ст. 1252.1: если в одном носителе несколько объектов, общая сумма составляет от минимума до двукратного максимума. Если предприниматель не знал о нарушении, компенсация 10–500 тыс. ₽. (Исправлено.) П. 3 ст. 1252 (снижение до 50% суммы минимумов) отменён. Постановление КС № 40-П от 24.07.2020 позволяет снижать двукратную стоимость права не ниже однократной при совокупности условий. Пределы стоит перепроверить у юриста ([Консультант](https://www.consultant.ru/document/cons_doc_LAW_64629/c2f79b53ce582e92680379e2ebd23eeb9fb7855a/)).
- **Судебная практика 2025–2026 годов (упрощённое производство):**
  - А53-5015/2025, 15 ААС, 09.09.2025: фрагмент YouTube-ролика в Instagram, 100 000 ₽ при иске 400 000. (Исправлено.) На момент фиксации автор указан не был, кадры использованы как иллюстрация, а не как цитата ([СудАкт](https://sudact.ru/arbitral/doc/doNZtLtRzYH4/)).
  - А48-11027/2024, АС Орловской области, 28.01.2025: чужая съёмка с квадрокоптера в рекламном ролике, 240 000 ₽ плюс 30 000 ₽ на юриста ([СудАкт](https://sudact.ru/arbitral/doc/itrcxdhqVt1S/)).
  - А39-6371/2025, АС Республики Мордовия, 15.01.2026: стоп-кадры ролика в Telegram, ВК и ОК, 240 000 ₽ ([СудАкт](https://sudact.ru/arbitral/doc/diLaGGB8FKdE/)).
  - А40-305533/2024, АС Москвы, 27.03.2025: с ВГТРК взыскано 160 000 ₽ ([СудАкт](https://sudact.ru/arbitral/doc/7wTsRwCi87Ws/)).
  Истцы — профессиональные авторы, которые подтверждают «цену права» лицензионными договорами.
- **Ст. 146 УК (133-ФЗ от 12.06.2024):** крупный размер — больше 500 тыс. ₽, особо крупный — больше 2 млн ₽. Ч. 2 — до 2 лет, ч. 3 — до 6 лет ([Консультант](https://www.consultant.ru/document/cons_doc_LAW_10699/b683408102681707f2702cff05f0a3025daab7ab/)). Уголовных дел о перезаливах на YouTube в исследовании не найдено.
- **Ст. 1257 ГК:** автором признаётся только гражданин. Дело о дипфейке А40-200471/2023 не подтверждено по первоисточнику.

**Разрыв между правом и правилами площадок.** Лицензия и соблюдение ст. 1274 ГК снимают претензии правообладателя, но не проверку оригинальности YouTube и Meta, которая «not based on copyright». Эти два контура нужно закрывать отдельно.

---

## 5. Технический стек нарезки

### 5.1 Open-source: сквозные клипперы

Данные GitHub API на 26.09.2026.

| Репозиторий | ★ | Последняя активность | Лицензия | Что делает | Вердикт |
|---|---|---|---|---|---|
| [mutonby/openshorts](https://github.com/mutonby/openshorts) | 5683 | push 25.09.2026 | MIT для ядра, папка `cloud/` — коммерческая лицензия; в зависимостях ultralytics (AGPL-3.0) | faster-whisper + PySceneDetect + Gemini/Ollama + YOLOv8/MediaPipe, субтитры, REST API, MCP, автопостинг через Upload-Post; на GPU 8-минутное видео обрабатывается ~1 мин против 5–8 мин на CPU | Лучшая основа для форка; ASR заменить на русский |
| [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) | 5146 | 17.09.2026 | MIT | faster-whisper, LLM (gpt-4o-mini / gemini-2.5-flash), OpenCV face tracking | Простой скелет на Python; API-режим завязан на платный MuAPI |
| [FujiwaraChoki/supoclip](https://github.com/FujiwaraChoki/supoclip) | 1195 | 23.09.2026 | AGPL-3.0 | AssemblyAI + LLM, virality score, B-roll; FastAPI + Next.js + ARQ | ASR только облачный; AGPL мешает закрытому SaaS |
| [NaufalRizqullah/opensource-clipping](https://github.com/NaufalRizqullah/opensource-clipping) | 126 | 26.09.2026 | MIT | Whisper large-v3 + Gemini + BlazeFace + pyannote, караоке-субтитры | Молодой проект, заточен под Colab T4 |
| [nmbrthirteen/podcli](https://github.com/nmbrthirteen/podcli) | 71 | 19.09.2026 | AGPL-3.0 (исправлено: так указано и на GitHub, и на сайте; коммерческая лицензия по запросу) | Клиппер подкастов, Remotion-субтитры, MCP на 26 инструментов | Для агентного управления |
| [xixihhhh/hotclip](https://github.com/xixihhhh/hotclip) | 252 | 23.09.2026 | AGPL-3.0 | Выбор моментов стрима по плотности чата | Идея полезна, проект заточен под китайские платформы |
| [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) | 543 | 17.01.2024 | MIT | Клипы по транскрипту и resize | Заброшен |
| [modelscope/FunClip](https://github.com/modelscope/FunClip) | 6346 | 01.09.2026 | MIT | Paraformer/SenseVoice + LLM | Русский не заявлен |

**Внимание.** В топике GitHub `ai-clip-generator` есть репозитории-приманки с накрученными звёздами: submagic-core (218★), clip-studio-ex-nexus (230★), invideo-ai-nexus (229★). В них нет исходного кода, а архив предлагают скачать с внешнего домена. Их нельзя запускать на серверах, где лежат токены соцсетей.

### 5.2 Компоненты

| Компонент | Репозиторий | ★ | Активность | Лицензия | Роль |
|---|---|---|---|---|---|
| Русский ASR | [salute-developers/GigaAM](https://github.com/salute-developers/GigaAM) (GigaAM-v3) | 827 | 17.08.2026 | MIT | CTC/RNNT с пунктуацией; longform (нужен pyannote и HF-токен); `word_timestamps=True` с апреля 2026; ONNX на CPU |
| Whisper-инференс | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 25 581 | v1.2.1 от 31.10.2025 | MIT | Стандарт; разработка замедлилась |
| Выравнивание по словам | [m-bain/whisperX](https://github.com/m-bain/whisperX) | 24 253 | v3.8.6 от 25.05.2026 | BSD-2 | Для русского по умолчанию `jonatasgrosman/wav2vec2-large-xlsr-53-russian`; на ARM Linux (исправлено) работает только на CPU и без torchcodec |
| Whisper на C++ | [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) | 53 938 | 24.09.2026 | MIT | CPU, связка с Remotion |
| Стабилизация таймкодов | jianfch/stable-ts | 2281 | архивирован 30.05.2026 | MIT | Не брать |
| Диаризация | [pyannote-audio](https://github.com/pyannote/pyannote-audio) | 10 594 | 4.0.7 от 30.06.2026 | MIT, модели по доступу | community-1: ~31 с на час на H100; на CPU 30–90 мин на час |
| Лёгкий ASR | [vosk-api](https://github.com/alphacep/vosk-api) | 15 148 | 09.08.2026 | Apache-2.0 | Только как запасной вариант |
| Детекция лиц | [google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe) | 37 083 | 25.09.2026 | Apache-2.0 | Детектор лиц работает; AutoFlip без поддержки с 01.03.2023 |
| Трекинг людей | [ultralytics](https://github.com/ultralytics/ultralytics) | 62 022 | 26.09.2026 | AGPL-3.0 / Enterprise | Для закрытого SaaS нужна Enterprise-лицензия |
| Активный говорящий | [Light-ASD](https://github.com/Junhua-Liao/Light-ASD) / TalkNet-ASD | 191 / 505 | 03.2025 / 10.2023 | MIT | Кроп следует за говорящим в подкастах на двоих и больше |
| Рефрейм | [KazKozDev/auto-vertical-reframe](https://github.com/KazKozDev/auto-vertical-reframe) | 21 | 19.04.2026 | MIT | Референс логики (YOLOv11-seg + ByteTrack) |
| Сцены | [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | 5201 | v0.7.1 от 21.07.2026 | BSD-3 | Сброс трекинга кропа на склейках |
| Тишина | [auto-editor](https://github.com/WyattBlue/auto-editor) | 5364 | 31.6.0 от 06.09.2026 | Unlicense | Предобработка стримов и вебинаров |
| Субтитры | [pycaps](https://github.com/francozanardi/pycaps) | 217 | 12.06.2026 | MIT | Альфа, не на PyPI |
| Рендер на React | [remotion](https://github.com/remotion-dev/remotion) + template-tiktok | 60 524 | v4.0.529 от 25.09.2026 | Собственная | Бесплатно для физлиц и компаний до 3 человек. Automators: $0,01 за рендер, минимум $100/мес. Creators: $25/мес за место (исправлено: «3 места включены» — неверно). Enterprise от $500/мес |
| Монтаж на Python | moviepy | 14 924 | v2.2.1 от 21.05.2025 | MIT | В v2 рендер медленнее: 18 мин 32 с против 1 мин 39 с на v1 ([issue #2395](https://github.com/Zulko/moviepy/issues/2395)) |
| Обёртка ffmpeg | ffmpeg-python | 11 010 | 04.08.2024 | Apache-2.0 | Не развивается; надёжнее subprocess или PyAV |

### 5.3 Качество русского ASR

**Данные разработчика** ([HF: ai-sage/GigaAM-v3](https://huggingface.co/ai-sage/GigaAM-v3)), WER в %:

| Набор | GigaAM v3 RNNT | GigaAM v3 CTC | T-One + LM | Whisper large-v3 |
|---|---|---|---|---|
| Open Datasets | 2,6 | 3,0 | 5,7 | 12,0 |
| Golos Farfield | 3,9 | 4,5 | 12,2 | 16,7 |
| Natural Speech | 6,9 | 7,8 | 14,5 | 13,6 |
| Callcenter | 9,5 | 10,3 | 13,5 | 23,9 |
| Disordered Speech | 19,2 | 20,6 | 51,0 | 59,3 |
| **Среднее по 5 наборам** | **8,4** | **9,2** | **19,4** | **25,1** |

(Исправлено: среднее считается по пяти наборам, включая Disordered Speech.) В расширенной таблице на 10 наборов, где Whisper взят с пост-обработкой, средние такие: 8,3 / 9,1 / 16,3 / 21,0. На OpenSTT YouTube — 10,6 / 11,6 / 21,9 / 17,8.

**Независимые замеры** ([berdachuk/stt-ru-benchmark](https://github.com/berdachuk/stt-ru-benchmark), 6–7.08.2026; у авторов есть коммерческий шлюз, так что независимы они только от Сбера):

| Домен | Whisper large-v3-turbo | GigaAM v3 | Deepgram Nova-2 | Nova-3 |
|---|---|---|---|---|
| Студийное чтение (n=775) | 4,44 | 4,50 | 3,97 | 6,55 |
| Аудиокниги (n=296) | 9,89 | 6,57 | 10,22 | 8,88 |
| Подкасты, интервью (n=20) | 7,73 | 6,95 | 9,27 | 7,12 |

Универсального победителя нет. На подкастах GigaAM впереди, но не в разы. Русские дообучения Whisper turbo дают ~10,7–10,8% против 13,25% у ванильной модели на шести наборах, по данным авторов ([coriollon](https://huggingface.co/coriollon/whisper-large-v3-turbo-russian)).

**Облачный ASR.** Yandex SpeechKit: асинхронный режим ~36,36 ₽ за час, отложенный ~9,14 ₽ за час (с НДС) ([тарифы](https://aistudio.yandex.ru/docs/ru/speechkit/pricing.html)). AssemblyAI: русский есть только в Universal-2 — $0,15 за час плюс $0,02 за диаризацию. (Исправлено.) Universal-3.5 Pro русский не поддерживает ([AssemblyAI](https://www.assemblyai.com/docs/pre-recorded-audio/supported-languages)).

### 5.4 Скорость

- **faster-whisper, официальный README** (13 минут аудио): RTX 3070 Ti, large-v2 fp16 batch 8 — 17 с; CPU i7-12700K (8 потоков), small int8 batch 8 — 51 с.
- **Turbo на GPU:** ~19 с на 13 минут, это около 1,5 минуты на час аудио. (Исправлено: это замер стороннего пользователя в issue #1030, GPU не указан, а не официальные данные.)
- **Надёжного CPU-замера turbo нет.** Оценка — 20–40 минут на час аудио на 8 потоках (оценка).
- **Кодирование:** по вторичным источникам NVENC в 5–15 раз быстрее libx264 на 1080p.

### 5.5 Коммерческие сервисы нарезки

| Сервис | Цена | API | Русский | Цена часа исходника |
|---|---|---|---|---|
| [OpusClip](https://www.opus.pro/pricing) | Free — 60 кредитов с водяным знаком; Starter $15/мес (150); Pro $29/мес или $174/год (300 в месяц). Неиспользованное переносится на 1 месяц (месячный план) или 2 месяца (годовой) — исправлено | Pro (beta), Max, Business; 30 запросов в минуту; 15 часов API-видео в месяц и 4 параллельных проекта на Pro и Max | Beta | ~$2,9–5,8 |
| [Vizard](https://docs.vizard.ai/docs/pricing) | Free 60 кредитов; Creator $29/мес или $174/год; Business $39/мес или $234/год (+$5 за место); 1 кредит = 1 минута | Во всех платных тарифах; модель clip_v2 стоит ×1,25 к clip_v1 (исправлено: множитель относится к модели, а не к «API v2») | Проверить | ~$3 |
| [Klap](https://docs.klap.app/pricing) | $14, $39, $94 (при оплате за год) | Оплата по факту: $0,32, $0,44, $0,48 за операцию; единица операции не расшифрована | Да (52 языка) | Зависит от операций |
| [Submagic](https://www.submagic.co/pricing) | $19, $39, $69/мес | Доп. минуты API $0,23–0,10 за минуту | Да | ~$6–14 |
| [Mirage (бывший Captions)](https://captions.ai/help/docs/api/pricing) | Подписка Max $24,99 | Субтитры $0,15 за минуту | — | ~$9 |
| Munch, Quso, 2short, CapCut | $9,9–220 | Публичного API нет | — | Не для конвейера; CapCut из РФ без VPN недоступен, а его ToS с 12.06.2025 даёт компании бессрочную лицензию на загруженный контент |

**Качество автоматического выбора моментов.** В обзорах собраны жалобы на обрезанные панчлайны и ошибки в субтитрах. В одном обзоре клипы с низким virality score обошли клипы с высоким в 18 из 20 пар ([eesel](https://www.eesel.ai/blog/opusclip-reviews), [autoposting.ai](https://autoposting.ai/blog/opus-clip-alternative)). Оценку «вирусности» от LLM стоит использовать как фильтр кандидатов и калибровать по собственной статистике досмотров.

### 5.6 Рекомендуемый стек

**Вариант А. Сервер без GPU (8 vCPU, 16 ГБ; оценка):**
1. Исходники получать от правообладателя файлами (ToS YouTube запрещает скачивать контент в обход функций сервиса), ffmpeg → аудио 16 кГц моно.
2. Для стримов и вебинаров — auto-editor.
3. ASR: GigaAM-v3 CTC через ONNX с `word_timestamps`, либо Yandex SpeechKit в отложенном режиме (~9 ₽ за час).
4. Выбор 5–15 фрагментов: Gemini Flash-Lite (из РФ API недоступен) или локальная Ollama по транскрипту с таймкодами; жёсткая JSON-схема, границы фрагментов по концам предложений.
5. PySceneDetect только на выбранных отрезках.
6. Кроп 9:16: MediaPipe или OpenCV YuNet, 5–10 кадров в секунду, сглаживание траектории (EMA). Диаризацию не включать.
7. Субтитры: генерация ASS с караоке-тегами и прожиг через libass в том же проходе, что и `libx264 -preset veryfast`; кириллические шрифты хранить локально (`fontsdir`).
8. Очередь RQ, ARQ или Celery; n8n использовать только для публикации.

Оценка времени на час исходника и ~10 клипов по 60 с: ASR 2–5 мин, LLM <1 мин, лица 1–3 мин, кодирование 3–6 мин, всего ~15–30 мин. С faster-whisper turbo на CPU — 45–70 мин (оценка, нужен замер).

**Вариант Б. GPU 16–24 ГБ (RTX 3090, 4090 или L4):**
- WhisperX на русском дообучении turbo (CTranslate2) или GigaAM-v3 RNNT; выравнивание через wav2vec2-ru.
- pyannote community-1 для подкастов с несколькими спикерами.
- MediaPipe или YOLO плюс Light-ASD.
- Кодирование через h264_nvenc; субтитры через ASS или Remotion.

Оценка: ~5–8 минут на час исходника. Аренда RTX 4090 на RunPod — $0,34/ч в Community Cloud, $0,74/ч в Secure Cloud (исправлено: не $0,69) ([RunPod](https://www.runpod.io/pricing)). Вычисления обходятся в $0,03–0,05, LLM — в $0,01–0,05, всего меньше $0,10 за час исходника против $3–15 у SaaS (оценка).

**Нагрузка.** Тяжёлый рендер не запускать на боевом сервере, где крутится сайт или бот: нужен отдельный воркер.

**Цены LLM** ([Gemini API](https://ai.google.dev/gemini-api/docs/pricing)): Gemini 3.6–3.8 Flash — $0,75/$3,75 за 1 млн токенов по промо-цене до 31.12.2026, потом $1,50/$7,50; 3.5 Flash-Lite — $0,30/$2,50; 2.5 Flash-Lite — $0,10/$0,40; Batch вдвое дешевле. Час русской речи — ~20–35 тыс. токенов (оценка). Россия и Беларусь не входят в список регионов Gemini API.

---

## 6. AI и faceless-генерация

### 6.1 Открытые генераторы «тема → готовый ролик»

| Проект | ★ | Активность | Лицензия | Что делает | Вердикт |
|---|---|---|---|---|---|
| [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | ~126 020 | v1.3.7 от 13.09.2026 (исправлено: не 02.09 — это дата v1.3.6), push 24.09.2026 | MIT | Сценарий через LLM (OpenAI, Claude, Gemini, DeepSeek, Qwen, Ollama и др.) → TTS (Edge, Azure, ElevenLabs, Chatterbox, Kokoro и др.) → стоки Pexels, Pixabay, Coverr или AI-клипы → субтитры → музыка; форматы 9:16, 16:9, 1:1; пакетный режим, WebUI, API, CLI, Docker; от 4 ядер и 4 ГБ, без GPU. В README заявлена прямая публикация в TikTok, Instagram и Shorts | Лучшая основа, но по умолчанию выдаёт шаблонную нарезку стоков — для монетизации нужен собственный авторский слой |
| [FujiwaraChoki/MoneyPrinterV2](https://github.com/FujiwaraChoki/MoneyPrinterV2) | 31 988 | 15.09.2026 | AGPL-3.0 | Слайдшоу из картинок Nano Banana 2 с озвучкой KittenTTS; загрузка на YouTube через Selenium и профиль Firefox | Формат совпадает с «image slideshows» из списка немонетизируемого; загрузка идёт мимо официального API |
| RayVentura/ShortGPT | ~7977 | последний коммит 10.02.2025 | MIT | Шортсы, длинные видео, дубляж | Заброшен |
| gyoridavid/short-video-maker | ~1377 | 21.06.2025 | MIT | MCP и REST, Kokoro, Pexels | Только английская озвучка |
| ddean2009/MoneyPrinterPlus | ~7144 | 07.03.2025 | GPL-3.0 | Комбайн генерации и публикации | Заброшен |
| SamurAIGPT/Text-To-Video-AI | ~832 | 24.08.2026 | MIT | Минимальный пайплайн | Годится как пример |
| linyqh/NarratoAI | ~11 208 | 17.09.2026 | MIT | Комментарии к фильмам на основе самих фильмов | Работает на чужом видео: зона авторских прав и reused content |
| elebumm/RedditVideoMakerBot | ~12 532 | 3.4.0 от 28.10.2025 | GPL-3.0 | Посты Reddit → TTS → фон | Контент принадлежит авторам постов; условия Reddit Data API (20.07.2026) запрещают его модифицировать и требуют отдельного договора для коммерции. Бот намеренно ничего не загружает сам: «to avoid community guideline issues» |

**n8n.** В каталоге по запросу «youtube shorts» 40 шаблонов, «reels» — 45, «faceless» — 11. Самый популярный — #5338, генерация на Seedance с публикацией через Blotato (~215 тыс. просмотров шаблона) ([n8n](https://n8n.io/workflows/5338)). Шаблоны на Sora 2 после 24.09.2026 не работают. Шаблоны-репостеры (#4199 «Auto-Repost TikTok Videos to YouTube Shorts», #2993 «Copy Viral Reels») построены именно на том сценарии, который ведёт к санкциям.

### 6.2 TTS, в том числе русский

| Движок | Лицензия / цена | Русский | Коммерческое использование |
|---|---|---|---|
| [Fun-CosyVoice3-0.5B](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512) (12.2025) | Apache-2.0 | Да, zero-shot клонирование | Да, нужен GPU |
| [Chatterbox Multilingual V3](https://github.com/resemble-ai/chatterbox) | MIT, 0,5B, 23 языка | Да | Да; в каждый файл встраивается водяной знак PerTh |
| [Silero](https://github.com/snakers4/silero-models) v5_cis_base / nostress | MIT | Да, голоса с префиксом `ru_`, ударения размечаются вручную | Да |
| Silero v5_ru и новее | CC BY-NC 4.0 | Да, с автоударениями | Нет, нужна отдельная лицензия |
| Kokoro-82M | Apache-2.0 | Нет | — |
| F5-TTS | Код под MIT, веса под CC-BY-NC | Нет официально | Нет |
| Fish Speech / S2 Pro | Fish Audio Research License | Да | Только по отдельному договору |
| XTTS-v2 | CPML, только некоммерческое | Да | Нет, и лицензию купить не у кого: Coqui закрыта |
| [edge-tts](https://github.com/rany2/edge-tts) | LGPLv3, бесплатно | Да | Неофициальный доступ к сервису Microsoft без договора; Microsoft уже ограничивала SSML |
| Azure Neural TTS | $15 за 1 млн символов, HD — $22; бесплатно 0,5 млн в месяц; по обязательствам $12→$6 за 1 млн (исправлено: $5,7 — цена для connected containers) | Да | Да |
| [ElevenLabs](https://elevenlabs.io/pricing/api) | API v3 — $0,10 за 1 тыс. символов, Flash/Turbo — $0,05; тарифы $6–990 в месяц; на бесплатном тарифе нет коммерческой лицензии | Да | С тарифа Starter |
| [Yandex SpeechKit](https://aistudio.yandex.ru/docs/ru/speechkit/pricing) | v1 — 1342 ₽ за 1 млн символов; v3 — 0,1626 ₽ за блок до 250 символов (~650 ₽ за 1 млн) | Да | Да, оплата в рублях |
| [SaluteSpeech](https://developers.sber.ru/docs/ru/salutespeech/tariffs/legal-tariffs) | 0,000186 ₽ за символ (~186 ₽ за 1 млн), минимум 15 000 ₽ в месяц; с 15.07.2026 новым клиентам пакеты и PAYG недоступны | Да | Да, условия уточнять |
| Gemini 3.8 Flash TTS | ~$0,00225 за 10 с до 31.12.2026, с 2027 года вдвое дороже | Да | Да, но API недоступен из РФ |

### 6.3 Видео- и картинко-модели

- **Sora 2 API закрыт 24.09.2026**, прямой замены нет. Разработчиков уведомили 24.03.2026 ([OpenAI](https://developers.openai.com/api/docs/models/sora-2)).
- **Veo 3.1** (Gemini API, со звуком): Standard — $0,40/с, 4K — $0,60/с; Fast — $0,10/0,12/0,30 за секунду; Lite — $0,05/с в 720p и $0,08/с в 1080p. Клипы по 4, 6 или 8 секунд, есть 9:16, водяной знак SynthID ([docs](https://ai.google.dev/gemini-api/docs/veo)).
- **Runway API** (шлюз к моделям разных вендоров): gen4_turbo — $0,05/с; seedance2 — $0,36–0,40/с; hailuo3 — $0,10–0,15/с; wan3 — $0,05–0,20/с ([Runway](https://docs.dev.runwayml.com/guides/pricing/)).
- **Kling v3 Pro** на fal.ai: $0,112/с без звука, $0,168/с со звуком.
- **Открытые модели.**
  - Wan 2.2 (Apache-2.0): TI2V-5B даёт 720p/24 fps на RTX 4090, для A14B нужно 80 ГБ VRAM.
  - HunyuanVideo 1.5 (от 14 ГБ VRAM): лицензия не действует в ЕС, Великобритании и Южной Корее.
  - LTX-2.x: бесплатно при годовой выручке до $10 млн.
- **Картинки для слайдшоу:** Nano Banana 2 Lite — $0,0336 за 1K (в batch $0,0168); Nano Banana 2 — $0,045–0,151; Gemini 3 Pro Image — ~$0,134.
- **Музыка:** Lyria 3.5 — $0,08 за трек.

### 6.4 Себестоимость 60-секундного ролика (~900–1000 символов закадра; ставки проверены, итоги — оценка)

| Формат | Себестоимость |
|---|---|
| Стоки + TTS | От $0 (edge-tts, без гарантий) до ~$0,10 (ElevenLabs v3); Azure ~$0,014; Yandex v3 ~0,65 ₽ |
| Слайдшоу из 8–12 AI-картинок + TTS | $0,13–0,8 |
| Полностью AI-видео (8–10 клипов по 6–8 с) | Veo 3.1 Lite 720p ~$3, 1080p ~$4,8; Veo Fast 1080p ~$7,2; Veo Standard ~$24; Kling v3 Pro со звуком ~$10; hailuo3 ~$6; wan3 $3–12; Seedance 2 $22–24 |
| С учётом перегенераций | ×1,5–3 (оценка) |
| Длинное видео на 8–10 минут со стоками | Озвучка ~$0,13 (Azure) или ~$0,9 (ElevenLabs v3) |

Сопоставление с доходом: пример YouTube — 1 млн engaged views Shorts дают автору $405. Полностью AI-видео по $3–24 за минуту окупается только в нишах и странах с высоким RPM длинных видео.

### 6.5 Правила площадок для AI-контента

- **YouTube.** Не монетизируются: «Generic or Repetitive Content» (слайдшоу, шаблоны, «AI-generated content made with generic or unoriginal templates»); склейки несвязанных ИИ-клипов ради шока; ИИ-персоны, выдающие себя за экспертов по здоровью, праву, финансам или политике; реалистичные фейки. (Исправлено: в правилах больше нет раздела с заголовком «Inauthentic content». Эти примеры стоят в разделе «Generic or Repetitive Content», а реалистичные фейки — в «Unsatisfying or Off-putting Content».) Разрешено: серии, где у каждого выпуска свой сюжет; ИИ как инструмент для сценария, заголовков и превью без обязательной метки; клон собственного голоса.
- **Раскрытие.** Для реалистичного синтетического контента и AI-музыки нужна метка. С 27.05.2026 YouTube ставит метки сам по C2PA и SynthID.
- **Instagram.** С 31.08.2026 профили, где главный герой — сгенерированный человек, обязаны носить метку «AI-generated profile». Без неё профиль могут сделать «non-recommendable». Метка на охват не влияет. Тем, кто просто использует ИИ в работе, метка не нужна ([Instagram](https://creators.instagram.com/blog/ai-generated-profile-label)). В API есть параметр `is_ai_generated=true` (с 22.06.2026).
- **Meta Translations** для Reels русский язык не поддерживают.

**Отчёты практиков о faceless-каналах (2026):**
- AI-сторителлинг на 240 тыс. подписчиков демонетизирован; автор пишет, что без лица или голоса ИИ использовать «impossible» ([Reddit](https://www.reddit.com/r/NewTubers/comments/1rkxvqv/could_you_show_me_some_ideas_for_channels_without/)).
- Канал на 50 тыс. с 2 роликами в месяц и полностью раскрытым ИИ отключён за «Inauthentic» и «Mass Produced» ([Reddit](https://www.reddit.com/r/NewTubers/comments/1rijozs/demonetized_for_mass_produced_ai_content_while/)).
- Sora-канал быстро вышел на монетизацию, а в апреле 2026 был исключён из YPP; его владелец опасается за основной канал на том же AdSense.
- Канал звуковых эффектов без ИИ (60 млн просмотров, 20–30 роликов в день) отключён за «mass production».
- Gold Product Expert на форуме YouTube о полностью ИИ-канале: «add a human voice over and ideally some original visuals».

По вендорским данным AIR, дорожки, озвученные только ИИ, удерживают зрителя в 4–5 раз хуже профессионального дубляжа. У AIR коммерческий интерес: они продают дубляж.

### 6.6 Автодубляж как мультипликатор своего контента

- **YouTube.** Автодубляж с 04.02.2026 доступен всем каналам, 27 языков. С русского дублирует только в английский. С английского — в 20 языков, включая русский, испанский, португальский и немецкий. Не применяется к роликам с заявками на авторские права и к роликам длиннее 120 минут ([YouTube Help 15569972](https://support.google.com/youtube/answer/15569972?hl=en)).
- **Охват.** В декабре 2025 года больше 6 млн зрителей в день смотрели не меньше 10 минут дублированного контента.
- **Multi-language audio.** Собственные дорожки для каналов с доступом к Advanced features дают в среднем больше 25% времени просмотра на неосновном языке ([blog.youtube](https://blog.youtube/news-and-events/multi-language-audio/)).
- **Засчитываются ли в YPP.** Прямого официального подтверждения, что часы дублированных дорожек идут в пороги YPP, нет. Из определения qualified watch hours это следует косвенно (оценка).

---

## 7. Автозалив: официальные API, лимиты, посредники

### 7.1 YouTube Data API v3

| Параметр | Значение | Источник |
|---|---|---|
| Квота на загрузку | С 01.06.2026 отдельная корзина: по умолчанию 100 вызовов `videos.insert` в сутки, 1 единица за вызов. `search.list` — тоже 100. Остальные методы — 10 000 единиц. Сброс в полночь по тихоокеанскому времени | [getting-started](https://developers.google.com/youtube/v3/getting-started), [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert) |
| История | 04.12.2025 загрузка подешевела с ~1600 до ~100 единиц. Расчёты «6 загрузок в день» устарели. Что стало с проектами, которым квоту расширяли раньше, не сказано | [revision history](https://developers.google.com/youtube/v3/revision_history) |
| Сопутствующие методы | `thumbnails.set` — 50, `playlistItems.insert` — 50, `videos.update` — 50, `captions.insert` — 400, `videos.list` — 1 | [quota cost](https://developers.google.com/youtube/v3/determine_quota_cost) |
| Реальный потолок | (Исправлено.) Обложка плюс плейлист — 100 единиц на ролик в общей корзине. 100 роликов съедают все 10 000 единиц без остатка, поэтому на практике ~95–98 роликов в сутки или без одной из операций | — |
| Неаудированный проект | «All videos uploaded via the videos.insert endpoint from unverified API projects created after 28 July 2020 will be restricted to private viewing mode». Апелляции нет, только перезаливка через проверенный сервис | [YouTube Help 7300965](https://support.google.com/youtube/answer/7300965?hl=en) |
| Аудит | Форма Audit and Quota Extension, срок не указан; по отзывам — недели и урезанные одобрения (не подтверждено) | [compliance audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits) |
| Политика разработчика | III.D.1.c — ровно один API-проект на одно приложение. III.I.2 — нельзя автоматизировать загрузки «without the user's prior specific and express consent». III.C.3 — пользователь сохраняет окончательный контроль над публикацией. III.E.1.a — нельзя скачивать и хранить аудиовизуальный контент YouTube. Доп. квоту можно тратить только на одобренный сценарий | [developer policies](https://developers.google.com/youtube/terms/developer-policies) |
| OAuth | В статусе Testing refresh-токен живёт 7 дней. Лимит — 100 refresh-токенов на аккаунт на клиент, старые аннулируются молча. Верификация чувствительных scope — «typically takes 3-5 business days» (исправлено: 2–4 недели — это вторичные источники) | [Google Identity](https://developers.google.com/identity/protocols/oauth2) |
| Shorts | Флага «это Shorts» нет: вертикальный или квадратный ролик до 3 минут YouTube определяет сам. Обложка через `thumbnails.set` для Shorts ненадёжна и на полку Shorts не попадает | [Issue Tracker](https://issuetracker.google.com/issues/381127084) |
| Поля | `publishAt` работает только с `privacyStatus=private`; `containsSyntheticMedia`; `selfDeclaredMadeForKids`; `paidProductPlacementDetails.hasPaidProductPlacement`; `brandPartner` (с 07.07.2026). Файл до 256 ГБ, resumable upload с чанками кратно 256 КБ | [videos](https://developers.google.com/youtube/v3/docs/videos) |
| Лимиты канала | По умолчанию до 15 минут без верификации. Суточный лимит загрузок официально не публикуется; по словам сообщества — ~10–20 роликов у новых каналов и 50–100 у старых. Ошибка `uploadLimitExceeded` | [YouTube Help 71673](https://support.google.com/youtube/answer/71673?hl=en) |
| Аналитика | `engagedViews` — основа для выплат. С 24.08.2026 публичный счётчик считает с первого кадра для всех форматов. Отчёты Reach с CTR обложек (с 15.01.2026). Reporting API хранит отчёты 60 дней. Денежные метрики — только для каналов YPP, иначе 403 | [Analytics revision history](https://developers.google.com/youtube/analytics/revision_history) |
| Несколько каналов | До 100 каналов на один Google-аккаунт через Brand Accounts, у каждого своя монетизация | [YouTube Help 3046356](https://support.google.com/youtube/answer/3046356?hl=en) |

### 7.2 Instagram Content Publishing API

- **Две схемы входа.** Instagram Login работает без страницы Facebook, но без тегов и рекламы. Facebook Login требует связки со страницей. Личные аккаунты публиковать через API не могут. Basic Display API закрыт 04.12.2024.
- **Процесс.** `POST /<IG_ID>/media` создаёт контейнер (`media_type=REELS`) → опрос `status_code` раз в минуту, не дольше 5 минут → `POST /<IG_ID>/media_publish`. Контейнер живёт 24 часа.
- **Лимит.** На странице Content Publishing: «limited to 100 API-published posts within a 24-hour moving period». Там же, в ограничениях для каруселей, и в справочнике `content_publishing_limit` указано 50. (Исправлено.) Meta противоречит сама себе, поэтому число не стоит зашивать в код: фактический лимит нужно запрашивать через `GET /<IG_ID>/content_publishing_limit` ([docs](https://developers.facebook.com/docs/instagram-platform/content-publishing/), [bundle.social](https://bundle.social/blog/instagram-api-rate-limits)). Blotato сам ограничивает Instagram 50 постами.
- **Требования к видео.** MP4 или MOV с moov-атомом в начале файла, H.264/HEVC, 23–60 fps, ширина до 1920 px, 9:16, видеобитрейт до 25 Мбит/с, AAC до 48 кГц, 128 кбит/с, длительность от 3 секунд до 15 минут, размер до 300 МБ.
- **Новое в 12.2025–06.2026.** Trial Reels через API (`trial_params`), удаление медиа, Audio API (оригинальные звуки и Sound Collection), `is_ai_generated`, до 3 соавторов в поле `collaborators`, лайки через API, счётчики reposts, saves и shares. Метрика `reels_skip_rate` показывает долю досмотров, прерванных в первые 3 секунды.
- **Доступ.** Для своих аккаунтов, добавленных в роли приложения, хватает Standard Access. Для чужих нужны Advanced Access, верификация бизнеса и App Review (2–6 недель по вторичным источникам, не подтверждено). Long-lived токен живёт 60 дней, продлевать можно токен старше 24 часов.
- **Неофициальные библиотеки** (instagrapi, 6849★) работают через приватный API. Сам README называет такую автоматизацию «fragile in production». Условия использования запрещают автоматизированный доступ «without our express permission». Практики сообщают о `challenge_required` и банах, особенно у аккаунтов моложе месяца ([instagrapi](https://github.com/subzeroid/instagrapi)). Массовые блокировки за «automation that doesn't follow our rules» фиксируются даже у новых аккаунтов без единого поста.

### 7.3 TikTok Content Posting API

- **До аудита** весь контент private. Публиковать могут не больше 5 пользователей за 24 часа, и только в приватные аккаунты (`SELF_ONLY`). Лимит — около 15 постов в сутки на автора, общий для всех API-клиентов. 6 запросов в минуту на токен ([TikTok](https://developers.tiktok.com/doc/content-posting-api-get-started)).
- **Запреты в Content Sharing Guidelines.** Запрещены «An app that copies arbitrary contents from other platforms to TikTok» и «A utility tool to help upload contents to the account(s) you or your team manages», а также приложения только для внутреннего использования ([guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines)). Собственное приложение для постинга в свои аккаунты аудит не пройдёт, реалистичный путь — аудированный посредник.
- **Для РФ.** Загрузка контента из России ограничена с 2022 года, с 2024 года частично открыта для тестовой группы. Монетизации нет.

### 7.4 Сторонние сервисы публикации

| Сервис | Цена | Модель | Замечания |
|---|---|---|---|
| [Upload-Post](https://www.upload-post.com) | Free (2 профиля, 10 загрузок, без TikTok); $24 (5 профилей), $50 (25), $147 (75), $438 (225) в месяц; при оплате за год — $16, $33, $118, $350 | Собственные одобренные интеграции с YouTube, Instagram и TikTok: не нужен ни свой Google-проект, ни App Review у Meta, ни аудит TikTok | 22 платформы, n8n, Make, MCP, встроенный FFmpeg API; обложки YouTube только для обычных видео, не для Shorts |
| [Zernio](https://zernio.com/pricing) (бывший Late) | С мая 2026 оплата за аккаунт: 1–2 бесплатно, 3–10 — $6, 11–100 — $3, 101+ — $1 в месяц | Официальные API | (Исправлено.) До апреля 2026 были пакетные тарифы (Build $19 … Unlimited $833) |
| [Blotato](https://www.blotato.com/pricing) | $29 (20 аккаунтов), $97 (40), $499 | «Official platform APIs only» | Instagram ограничен 50 постами за 24 часа; генерация API-ключа сразу включает платный тариф |
| [Ayrshare](https://www.ayrshare.com/pricing/) | $149 (1 профиль), $299 (10), от $599 (30) | Официальные API | Рассчитан на продукты и агентства |
| [Postiz](https://github.com/gitroomhq/postiz-app) (~36,3 тыс. ★, AGPL) | Облако $29–99; self-host бесплатно | При self-host нужны свои приложения и аудиты у Google, Meta и TikTok | v2.24.0 от 22.09.2026 |
| Mixpost (MIT Lite) | Pro — $299 разово | Self-host | Lite не умеет YouTube, Instagram и TikTok |
| Buffer | $5–10 за канал | GraphQL API, 3000–15 000 запросов за 30 дней | Небольшие объёмы |
| Metricool / Publer | От €43 / ~$21 | API на старших тарифах | — |
| [SMMplanner](https://smmplanner.com/) | От 990 ₽/мес (660 ₽ при оплате за год) | VK, OK, Telegram, MAX, TikTok, Instagram | «Расширенный» способ подключения Instagram — эмуляция Android-устройства, то есть неофициальный путь; открытого API нет |
| [Postmypost](https://postmypost.io/ru/prices) | От 1190 ₽/мес | 22 площадки, включая YouTube, Rutube и MAX | API — отдельный модуль |

**Риск посредников.** У сервисов вроде Upload-Post все аккаунты клиентов висят на одном аудированном приложении. Если платформа отзовёт доступ, пострадают все клиенты сразу.

### 7.5 Управление множеством каналов в рамках правил

- **YouTube:** Brand Accounts с ролями владельца и менеджера, до 100 каналов на Google-аккаунт. Один API-проект на приложение. Явное согласие владельца каждого канала на автоматическую загрузку. Решения по публикации остаются за пользователем.
- **Meta:** бизнес-портфолио с Full и Partial access и ролью API Integration Access. Официальный предел Instagram-аккаунтов на портфолио не подтверждён (во вторичных источниках — 25).
- **Санкции распространяются на все связанные каналы и аккаунты владельца** (раздел 3.4). Надёжность сети определяется контентом каждого отдельного канала.
- **Токены:** 7 дней у Google в режиме Testing, 60 дней у Instagram. Без мониторинга продления автоматизация тихо ломается.

---

## 8. Баннерная реклама и монетизация

### 8.1 Форматы: что разрешено и что запрещено

**YouTube.**
- **Запрещено:** «Can I burn video ads (pre-rolls, mid rolls, and post rolls) into my videos? No. YouTube's ads policies don't let you burn or embed advertiser-created and supplied video ads or other commercial breaks into your content» ([YouTube Help 17596007](https://support.google.com/youtube/answer/17596007?hl=en)). В другой справке: «YouTube creators cannot include promotions, sponsorships, or other advertisements for third-party sponsors or advertisers in their videos where YouTube offers a comparable ad format, including… pre, mid, and post rolls, and video bumpers». За нарушение YouTube может отключить монетизацию или удалить ролик ([YouTube Help 3364658](https://support.google.com/youtube/answer/3364658?hl=en)).
- **Разрешено:** продакт-плейсмент, endorsement, спонсорский сегмент, который автор делает сам. Обязательна отметка «My video contains paid promotion». Правило действует для видео, описаний, комментариев, стримов и Shorts. Нераскрытую интеграцию система может пометить сама. Такие ролики удаляются из YouTube Kids, а рекламу конкурентов спонсора на них могут не показывать.
- **Статичный вшитый баннер-оверлей** в правилах прямо не назван. Любая оплаченная вставка при этом считается branded content: её нужно раскрыть, и она должна соответствовать Google Ads policies.
- **Категории.** Запрещены наркотики, оружие, хакерский софт, контрафакт, сервисы написания эссе. С ограничениями — алкоголь, финансы, медицина, азартные игры, политика: партнёр должен быть сертифицирован Google, нужны гео- и возрастные ограничения.
- **Dynamic brand segments** — пилот 2026 года по приглашениям. Сменный спонсорский сегмент от 30 секунд до 3 минут, «Creator-led content. Third-party video ads or announcer-read ads are prohibited». Сегмент ставится в длинные ролики от 8 минут, максимум в 20 роликов, с гео-таргетингом, бренд подтверждает его в Google Ads. Для Shorts не предназначен ([YouTube Help 17415761](https://support.google.com/youtube/answer/17415761?hl=en)).
- **Creator Partnerships** (бывший BrandConnect): только для участников YPP из 24 стран, России в списке нет ([YouTube Help 9385307](https://support.google.com/youtube/answer/9385307?hl=en)).
- **Open Call:** США, Великобритания, Индия; 1000–500 000 подписчиков; «Videos featuring AI-generated visuals… may be automatically rejected»; оплата через AdSense в течение 60 дней ([YouTube Help 17210205](https://support.google.com/youtube/answer/17210205?hl=en)).

**Meta.** Правила Branded Content ([Meta](https://www.facebook.com/business/help/221149188908254)):
- «Don't include pre, mid, or post-roll ads in videos… Don't include banner ads in videos or images. Don't include title cards within a video's first three seconds». Промежуточные и финальные карточки — не дольше 3 секунд подряд.
- Определение баннера: «a branded column (often horizontal or vertical) that is overlaid onto and visually separated from the original image or video content» ([Meta](https://www.facebook.com/business/help/1190980254246452)).
- Публикация только через branded content tool с отметкой партнёра.
- «Creators cannot accept anything of value to post content that does not feature themselves or that they were not involved in creating».
- Гемблинг, криптовалюты, дейтинг и аптеки — только с письменного разрешения Meta. MLM, ICO и бинарные опционы запрещены.
- Creator Marketplace: от 1000 подписчиков, «not available in… Russia».

**TikTok.** Branded Content Policy действует с 31.08.2026: для оплаченного контента нужно включать переключатель раскрытия коммерческого контента. TikTok One — от 10 000 подписчиков, 1000 просмотров и 3 постов за 30 дней ([TikTok](https://www.tiktok.com/legal/page/global/bc-policy/en)).

**RUTUBE.** В правилах размещения прямо предусмотрены нативные интеграции и рекламные врезки (midroll). Рекомендовано размещать `erid: XXX` поверх видео во время врезки. Обязательны пометка «Реклама», данные рекламодателя и токен ([rutube.ru/info/adverguide](https://rutube.ru/info/adverguide/)). Запрета на вшитые врезки в правилах нет, есть требования к маркировке по закону.

### 8.2 Как вести трафик при некликабельных ссылках

- **YouTube Shorts.** С 31.08.2023 URL в описаниях и комментариях Shorts некликабельны: «To reduce spam and scam attempts…» ([YouTube Help 13748639](https://support.google.com/youtube/answer/13748639?hl=en)). Кликабельны:
  - ссылки в профиле канала (до 14, первая стоит рядом с кнопкой Subscribe);
  - «Related video» из Short на своё длинное видео (нужен доступ к расширенным функциям);
  - упоминания и хэштеги;
  - партнёрские ссылки YouTube Shopping;
  - ссылка на сайт бренда в Shorts, но только если ролик входит в заявленную кампанию и ведёт прямо на сайт партнёра;
  - кликабельный стикер бренда в Shorts, если сделка заявлена через YCP API или Google Ads;
  - в длинных видео — ссылки в описании и конечные заставки.
- **Ограничение спам-политики.** Контент, созданный «solely to drive users off of YouTube to external sites», считается нарушением, особенно если ведёт на запрещённые материалы.
- **Instagram.** Ссылки в подписях к Reels некликабельны. Работают ссылки в био (до 5), стикер-ссылка в Stories, промокоды, призыв «ссылка в профиле».
- **Конверсия воронок в РФ.** Shorts → Telegram: 0–40 переходов на 20 тыс. просмотров, хуже, чем в Instagram и TikTok ([vc.ru](https://vc.ru/1761968)). Telegram с 10.02.2026 замедляют; просмотры постов 15 крупнейших брендов в I квартале 2026 года упали на 23,5% ([vc.ru](https://vc.ru/2952003)).
- **Официальная воронка Shorts → длинные видео.** По словам YouTube, у форматов раздельные рекомендательные системы, и «we haven't seen evidence of Shorts negatively impacting long-form performance». Официального коэффициента конверсии нет. Гайд YouTube от 14.07.2026 признаёт, что зрители, которые смотрят только Shorts, «may never naturally click on a 10-minute video». Рекомендации гайда: призыв к действию в последние 5 секунд Short и Related video ([blog.youtube](https://blog.youtube/creator-and-artist-stories/youtube-related-videos-traffic-guide/)). По корреляционным данным AIR (18 000 англоязычных каналов), оптимальная доля Shorts — 25–40% загрузок, а выше 55% длинные видео проседают ([AIR](https://air.io/en/audience-growth/do-youtube-shorts-help-your-long-form-videos-grow-data-from-18000-channels)).

### 8.3 Техника вставки интеграций и трекинг

- **FFmpeg.** Фильтр `overlay` с параметром `enable='between(t,5,10)'` показывает плашку или логотип только в нужном интервале. `drawtext` с `textfile` и `reload` подставляет текст под конкретного рекламодателя. Так один шаблон даёт A/B-варианты для пакетного рендера ([ffmpeg filters](https://ffmpeg.org/ffmpeg-filters.html)).
- **Шаблонизаторы:** Remotion (React, условия лицензии см. в 5.2), Shotstack ($0,20–0,30 за минуту рендера, зарубежный сервис), Editly (MIT, развивается медленно).
- **Трекинг:** UTM-метки и короткие ссылки для каждого рекламодателя и варианта, отдельные промокоды. Промокоды используют 45,9% маркетологов, affiliate-ссылки — 26,0% ([IMH Benchmark 2026](https://influencermarketinghub.com/influencer-marketing-benchmark-report/)). Для YouTube-интеграций бренд через Brand partner access видит органические и платные метрики ролика ([YouTube Help 15672082](https://support.google.com/youtube/answer/15672082?hl=en)).
- **РФ:** erid в креативе или рядом со ссылкой; статистика показов и акты передаются в ЕРИР через ОРД до 30-го числа следующего месяца. Один креатив — один erid, для другой площадки новый токен не нужен ([ОРД-А](https://ord-a.ru/wiki/key_points/)).

**Как формат соотносится с правилами.** Авторская интеграция с раскрытием разрешена на всех площадках. Вшитые готовые рекламные ролики или паузы рекламодателя на YouTube запрещены. Баннерные полосы поверх видео запрещены у Meta. RUTUBE разрешает врезки с erid. Если реклама вставляется на уровне сети в чужой контент, к ней добавляются ограничения Meta: плата за публикацию контента, в создании которого автор не участвовал, запрещена.

### 8.4 Экономика

**RPM длинных видео по нишам** ([AIR, 300 каналов, май 2025 – май 2026](https://air.io/en/air-data-findings/which-youtube-niche-makes-the-most-money-in-2026-ranked-by-real-rpm-and-cpm), данные вендора; география выборки не раскрыта):

| Ниша | Медианный RPM | P25–P75 |
|---|---|---|
| Education & Science | $10,22 | $2,31–19,50 |
| Transport* | $5,69 | — |
| Lifestyle | $2,98 | — |
| News & Politics* | $2,60 | — |
| Entertainment | $2,43 | $0,88–5,02 |
| Gadgets & Tech | $2,33 | $0,70–3,71 |
| Gaming | $2,05 | — |
| Business & Finance* | $2,01 | $0,73–5,17 |
| Health & Sport | $1,23 | — |
| Kids & Teens | $0,33 | — |
| **Все ниши** | **~$2,30** | — |

\* меньше 10 каналов в выборке. Топ-10% каналов забирают 58% рекламного дохода. Декабрьский CPM может быть на 40–60% выше январского.

**RPM Shorts по странам, 2025 год** ([AIR, 23.01.2026](https://air.io/en/monetization/what-rpm-can-you-expect-from-shorts-in-2026)): США $0,328; Швейцария $0,205; Австралия $0,193; Южная Корея $0,185; Великобритания $0,166; Канада $0,165; Германия $0,163; Франция $0,102; Бразилия $0,045; Мексика $0,040; Турция $0,024; Индонезия $0,012; Индия $0,008. RPM Shorts составляет 3–14% от RPM длинных видео, а у большинства каналов Shorts дают меньше 2% дохода.

**Как читать цифры по Shorts.** RPM считается на 1000 engaged views. Публичный счётчик считает каждый старт, поэтому модель по публичным просмотрам завышает доход в 2–4 раза. Пример практика: 1 млн публичных просмотров → 277,7 тыс. engaged → $11,65 ([Reddit](https://www.reddit.com/r/PartneredYoutube/comments/1oau67h/my_short_hit_1m_views_in_2_days_but_only_earned/)). Другие ориентиры:
- TubeBuddy: 3,1 млн просмотров → $99,87 (~$0,032 за 1000); реалистично $10–50 за 1 млн публичных просмотров;
- Mediacube: $0,01–0,05 за 1000 (исправлено: не $0,01–0,08; цифр $0,15–0,25 для финансов и технологий в источнике нет);
- ytmoneycalculator: $0,03–0,08 (исправлено);
- практики: 820 тыс. просмотров → $128 (август 2026, аудитория в основном из США); канал на 12 млн просмотров Shorts в месяц — лучший месяц ~$2000 (за период с 2022 года).

**CPM по странам (длинные видео).** Таблицу AIR (США $14,67, Германия $9,79, Великобритания $8,91, Бразилия $1,64, Мексика $1,82, Турция $1,09, Индия $0,74, Украина $1,77) стоит считать низкодостоверной: одни и те же цифры повторяются в статьях 2024, 2025 и 2026 годов. У агрегаторов оценки расходятся в 2–5 раз: Mediacube даёт для США $20–42, Influencer Marketing Hub — явно ошибочные $0,38. Надёжны только собственные данные Studio. Для русскоязычного контента вне РФ публичных датированных RPM нет.

**Интеграции (англоязычный рынок, вилки агрегаторов 2026 года, методология не раскрыта):**
- YouTube — ~$50–100 за 1000 просмотров. За видео: nano $100–500, micro $500–5000, mid $5000–10 000+ ([IMH](https://influencermarketinghub.com/influencer-rates/)).
- Instagram: nano $10–1000, micro $1000–5000, mid $5000–10 000+ за пост. TikTok: micro $1000–5000.
- Hootsuite — ориентир «$100 per 10,000 followers». Shopify — у Shorts «lower rates».
- ~80% ставок у nano, micro и UGC-авторов ниже $500 (IMH Benchmark 2026).
- ThoughtLeaders: спонсорство в Shorts — «still developing… less standardized», обычно короткое устное или экранное упоминание. Бренды в II квартале 2026 года ищут авторов на 50–500 тыс. подписчиков.
- Проверенного первичного CPM именно для интеграций в Shorts и Reels не найдено.

**Биржи.** Passionfroot: для автора бесплатно, бренд платит 2%, при подборе бренда площадкой — 15%. ThoughtLeaders: для авторов без комиссии, в партнёрскую программу проходит ~5% сети.

**Россия, реальные ставки:**

| Источник | Метрика |
|---|---|
| VK AdBlogger, кейс Ukids (май–июль 2026) | Посевы в сообществах от 50 тыс. подписчиков с охватом от 5 тыс., CPM не выше 40–45 ₽; 28 000 размещений в 2500 сообществах; ROMI +27%. В кейсе две суммы расходов — ~21,5 млн ₽ и 2,48 млн ₽ (расхождение не выяснено) ([adblogger.vk.ru](https://adblogger.vk.ru/top-cases/ukids)) |
| VK AdBlogger, кейс Drivee (2025) | CPV 0,37 ₽ (64 публикации, охват 414 930); в малом городе 1,13 ₽; отбор только сообществ с меткой РКН «А+» ([adblogger.vk.ru](https://adblogger.vk.ru/top-cases/drivee)) |
| Яндекс Директ, Telegram | Средний CPV 1 ₽ (от 0,5 до 4 ₽ по тематикам); CPC 92 ₽, CTR 0,23%; 18 000+ каналов в РСЯ ([Яндекс](https://yandex.ru/project/direct/performance/promo_telegram/direct_tg_reach_and_perf)) |
| Сбер Developers (вторичный источник) | CPM в Telegram 50–500 ₽, Rutube 50–90 ₽ (низкая достоверность) |
| VK Видео | ~30–90 ₽ за 1000 просмотров (оценка vc.ru, низкая достоверность) |
| Клипы | Klipni — CPM задаёт бренд; стримеры — $17,5 за 100 тыс.; UGC-тендеры — CPV 0,1 ₽ |

**CPA и маркетплейсы офферов.** Perfluence (от 2000 подписчиков, в основном CPA), GetBlogger (от 1000), Яндекс ПроБлогер: маркетплейс офферов для Telegram, MAX, Дзена, VK, YouTube и Rutube; вознаграждение за целевое действие в течение 21 дня после перехода.

**Telegram Ads.** Официально владельцу канала от 1000 подписчиков положено 50% выручки, вывод через Fragment в Stars или Gram ([telegram.org](https://telegram.org/tos/content-creator-rewards)). Российские каналы получили монетизацию 19.07.2024 и потеряли её 24.07.2024, данных о возвращении нет ([vc.ru](https://vc.ru/1331575)). Условия оговаривают, что программа «may be partially or fully unavailable to certain… geographical regions».

**Пример расчёта (оценка).** Сеть из 10 оригинальных Shorts-каналов на английском с аудиторией из США и Европы по 3 млн engaged views в месяц — всего 30 млн. При RPM $0,16–0,33 это $4,8–9,9 тыс. в месяц до 01.02.2027. После 01.02.2027 каналы с 9 млн за 90 дней (меньше 10 млн) не получат дохода из пула. Та же сеть с продажей одной интеграции в месяц на канал по $100–500 (nano/micro) даёт $1–5 тыс. независимо от YPP. Для российской аудитории доход YouTube равен нулю, а продажа интеграций российским рекламодателям на YouTube с 01.01.2027 рискованна по позиции ФАС.

---

## 9. Юридическая часть для оператора из РФ

### 9.1 Статус YouTube в РФ и доступ к монетизации

- **Доступ.** Замедление с июля–августа 2024 года. 10–12.02.2026 домены YouTube удалены из НСДИ, без VPN сервис недоступен ([DW](https://www.dw.com/en/youtube-whatsapp-blocked-in-russia/a-75940102)). Формально YouTube в реестр блокировок не внесён ([iStories](https://istories.media/news/2026/03/06/sledom-za-telegramom-fas-i-rkn-nazvali-reklamu-na-youtube-zapreshchennoi/)). 31.03.2026 Мосгорсуд признал ограничения законными. Дневная аудитория — 22,4 млн (январь–май 2026).
- **Монетизация.** С 03.03.2022 реклама Google не показывается пользователям в России. С 10.03.2022: «creators in Russia won't be able to complete new YPP sign-ups». Каналы, привязанные к российским AdSense, монетизировать нельзя. Последняя выплата российским AdSense прошла за июль 2024 года ([YouTube Help 7101720](https://support.google.com/youtube/answer/7101720?hl=en), [BleepingComputer](https://www.bleepingcomputer.com/news/google/google-deactivates-russian-adsense-accounts-sends-final-payments/)).
- **Требование к месту жительства.** Условие YPP — «Live in a country/region where the YouTube Partner Program is available». Страну платёжного профиля Google изменить нельзя, банк должен быть в той же стране, код телефона должен совпадать со страной AdSense ([Google Payments](https://support.google.com/paymentscenter/answer/7161426?hl=en), [AdSense](https://support.google.com/adsense/answer/9905?hl=en)). В списке YPP (138 стран и территорий; исправлено: не ~177) есть Казахстан, Армения (с 11.05.2026), Грузия, Азербайджан, Молдова, Израиль, страны Балтии. Нет России, Беларуси, Узбекистана и Кыргызстана.
- **Практика «дружественных» AdSense.** Практики сообщают о блокировках за «несовпадение страны», заморозке выплат, каскадном удалении каналов на посредническом AdSense ([konoden.ru](https://konoden.ru/monetizacziya-youtube-v-rossii-2025-2026-realnye-problemy-i-resheniya/), [searchengines.guru](https://searchengines.guru/ru/forum/1133987)). Официального текста Google о монетизации через иностранное юрлицо, чьи управляющие живут в РФ, не найдено. Есть только требование «live in» и формулировка «be who they say they are».
- **Налог США.** Соглашение США и России приостановлено с 16.08.2024. Для налогового резидента РФ удержание с дохода от зрителей из США — 30% ([IRS](https://www.irs.gov/businesses/international-businesses/russia-tax-treaty-documents)).

### 9.2 Маркировка рекламы (ОРД, erid, ЕРИР)

- **Ст. 18.1 38-ФЗ.** Нужны пометка «реклама», указание рекламодателя или ссылка на сведения о нём, идентификатор (erid) от ОРД. Сведения передаются в ЕРИР через ОРД. Слово «реклама» обязательно, формулировки вроде «рекламная информация» не подходят. Порядок размещения идентификатора установлен приказом РКН № 68 от 25.03.2025 ([Консультант](https://www.consultant.ru/document/cons_doc_LAW_58968/2c4537e4796f6ff8b2736ed1b0d4fef08e14458e/)).
- **Штрафы по ст. 14.3 КоАП** (не 14.32; проверено по редакции от 26.07.2026), для граждан / должностных лиц / юрлиц:
  - ч. 1 — общее нарушение: 2–2,5 тыс. / 4–20 тыс. / 100–500 тыс. ₽;
  - ч. 15 — сведения не переданы в ЕРИР: 10–30 тыс. / 30–100 тыс. / 200–500 тыс. ₽;
  - ч. 16 — нет идентификатора: 30–100 тыс. / 100–200 тыс. / 200–500 тыс. ₽;
  - ч. 18 — реклама средств обхода блокировок: 50–80 тыс. / 80–150 тыс. / 200–500 тыс. ₽;
  - ч. 7–10 — реклама иностранных лиц или на их ресурсах под запретом РКН: до 3–6 млн ₽ для юрлиц.
  За январь–май 2025 года вынесено 230 постановлений за нарушения маркировки на 13,08 млн ₽ (исправлено: это маркировка вообще, а не только реклама у незарегистрированных блогеров).

### 9.3 Рекламный сбор 3%

Ст. 18.2 38-ФЗ (479-ФЗ), действует с 01.04.2025. Платят все рекламораспространители, операторы рекламных систем и посредники, включая самозанятых и физлиц-блогеров, — 3% квартального дохода от рекламы, адресованной потребителям в РФ. Блогер платит с полной суммы, агентство — с комиссии. Порядок утверждён постановлением Правительства № 1224 от 15.08.2025 (исправлено: не 15.01.2025). РКН формирует расчёт не позднее 15-го числа второго месяца после квартала, плательщик подтверждает его до 25-го числа, платит до 5-го числа третьего месяца ([PGPLaw](https://www.pgplaw.ru/analytics-and-brochures/alerts/pravitelstvo-utverdilo-poryadok-uplaty-obyazatelnykh-otchisleniy-za-reklamu-v-internete-/), [eLama](https://elama.ru/blog/faq-po-sboru-3-za-dohod-ot-reklamy-otvechayut-eksperty-elama/)). Если российский рекламодатель заключает договор с иностранцем, сбор платит сам рекламодатель.

### 9.4 Запрет рекламы в Instagram и на ресурсах с ограниченным доступом

- **Норма.** Ч. 10.7 ст. 5 38-ФЗ (72-ФЗ от 07.04.2025) с 01.09.2025 запрещает рекламу на ресурсах нежелательных, экстремистских и запрещённых организаций и «на других информационных ресурсах, доступ к которым ограничен». Это прямо касается Instagram и Facebook. Дата 01.09.2024, которая иногда встречается, ошибочна.
- **Первые дела.**
  - Юрист-блогер из Краснодара — 30 000 ₽ (октябрь 2025) за видео об отеле с промокодом; основание обжалуется ([Svoboda](https://www.svoboda.org/a/bloger-yurist-iz-krasnodara-poluchila-shtraf-za-reklamu-v-instagrame/33566581.html)).
  - Омское УФАС против блогера Аси Сивоконевой (21 тыс. подписчиков): рилс от 03.09.2025 квалифицирован по ч. 10.7, рилс от 26.08.2025 — по ч. 16 ст. 18.1. Блогер признана и рекламодателем, и распространителем. Итог дела не найден. (Исправлено: штраф 30 тыс. ₽ из Habr относится к краснодарскому делу, а не к этому.)
- **Законопроект Выборного** об увеличении штрафов (до 1 млн ₽ для юрлиц) не принят.
- **Сопутствующие риски.** Кунцевский районный суд (14.07.2025) признал, что оплата верификации Instagram может «contribute to the financing of an extremist organization» (ст. 282.3 УК) ([Moscow Times](https://www.themoscowtimes.com/2025/08/22/instagram-verification-counts-as-financing-extremism-russian-court-says-a90329)). Ст. 13.53 КоАП — 3000–5000 ₽ за умышленный поиск экстремистских материалов. Ч. 10.8 ст. 5 (332-ФЗ) запрещает рекламу VPN.
- **Что остаётся.** Личное пользование Instagram не запрещено. Модель «Reels как канал трафика на зарубежную аудиторию» формально возможна. Продажа там рекламы российским рекламодателям — нет.

### 9.5 Реклама в YouTube и Telegram

5–10.03.2026 ФАС заявила, что реклама в Telegram и YouTube нарушает ч. 10.7 ст. 5, и возбудила дела: против Александры Посновой (YouTube) и Марии Лоскутовой (Telegram, реклама была промаркирована и шла по договору). 25.03.2026 объявлен переходный период: до 31.12.2026 ответственность за рекламу именно в Telegram и YouTube не применяется. На Instagram, Facebook и VPN мораторий не распространяется. Маркировка, ЕРИР и сбор 3% остаются обязательными. Что будет с 2027 года, ФАС прямо не сказала ([rb.ru](https://www.rb.ru/news/fas-dala-oficialnoe-razyasnenie-za-reklamu-v-telegram-i-youtube-ne-budut-shtrafovat-do-2027-goda/), [f-jurist](https://f-jurist.ru/blog/reklama-v-telegram-zapreshchena-ili-net-pozitsiya-fas-shtrafy-i-perekhodnyy-period-do-kontsa-2026/)). Штраф для юрлица по ч. 1 ст. 14.3 — до 500 тыс. ₽. Отвечают и рекламодатель, и распространитель.

### 9.6 Реестр блогеров, иноагенты

- **Реестр 10 тыс.+.** С 01.11.2024 владельцы страниц с аудиторией больше 10 000 обязаны подать сведения в РКН в течение 10 рабочих дней после того, как перешли порог. С 01.01.2025 рекламу на незарегистрированной странице размещать нельзя, донаты и репосты из неё тоже запрещены (ч. 10.6 ст. 5 38-ФЗ, ст. 10.6 149-ФЗ; 303-ФЗ). Реестр охватывает Telegram, VK, OK, Дзен, YouTube, Rutube, TikTok, Twitch и другие площадки. Для сети анонимных пабликов легальная продажа рекламы невозможна: каждый канал от 10 тыс. регистрируется отдельно.
- **Иноагенты.** Ч. 10.5 ст. 5 запрещает рекламу на ресурсах иноагентов, п. 12 ст. 7 — рекламу самих этих ресурсов (42-ФЗ от 11.03.2024). Ч. 10.4 — реклама на ресурсах иностранных лиц, если РКН принял решение о запрете.

### 9.7 Азартные игры и криптовалюта

- **Азартные игры.** Ст. 27 38-ФЗ: рекламировать можно только лицензированных организаторов. В интернете реклама букмекеров допускается только в спортивных сетевых СМИ, на сайтах спортивных федераций и лиг и на сайтах учредителей спортивных телеканалов. Онлайн-казино в РФ нелегальны, их реклама незаконна. Ч. 1.1 (41-ФЗ от 20.02.2026) требует предупреждения о зависимости на 7% площади (дату вступления нужно проверить).
- **Правила платформ по гемблингу.** YouTube с 19.03.2025 удаляет контент с логотипами и рекламой несертифицированных казино. Meta пускает real money gambling только с письменного разрешения. В российском сегменте нарезки с «баннерами ставок» и кейс-сайтами CS2 попадают сразу под оба ограничения.
- **Криптовалюта.** Её реклама запрещена (п. 13 ст. 7), кроме услуг организаторов обращения по новой ст. 29.2 (283-ФЗ от 04.08.2026). Реклама должна содержать предупреждение о риске потери средств; упоминать конкретные монеты и обещать доходность нельзя.

### 9.8 Получение денег из-за рубежа: валютный контроль, налоги, санкции

- **Платёжные сервисы.**
  - Stripe, Wise, PayPal: Россию не обслуживают. Wise: в России нельзя зарегистрироваться, войти в аккаунт и пользоваться картой.
  - Whop: не проводит KYC для людей в России. Content Rewards платит только через Whop.
  - Payoneer: статус для резидентов РФ не подтверждён.
  - EO 14071: с 08.05.2022 американским компаниям запрещено оказывать людям в России услуги по регистрации компаний, с 12.09.2024 — ещё и услуги IT.
  - ЕС с 2022 года запрещает гражданам и резидентам РФ услуги криптокошельков.
- **Валютный контроль** (ст. 12 173-ФЗ, в редакции от 04.08.2026):
  - деньги от нерезидентов можно без ограничений зачислять на счета в банках стран ЕАЭС и стран, с которыми РФ автоматически обменивается финансовой информацией (ч. 5.2);
  - об открытии счёта нужно уведомить налоговую в течение месяца;
  - ОДДС сдаётся до 1 июня, по счетам в ЕАЭС и странах обмена — только если за год зачислено больше 600 тыс. ₽;
  - на физлиц, которые провели за границей больше 183 дней в году, эти обязанности не распространяются.
  Штрафы по ст. 15.25 КоАП: незаконная валютная операция — 20–40% суммы; уведомление не подано — 4000–5000 ₽ ([Консультант](https://www.consultant.ru/document/cons_doc_LAW_45458/15452676240afa2b4ce846308d8f9434eaac4019/)).
- **Криптовыплаты** (Clipping.net, Fragment). С 01.09.2026 цифровая валюта — валютная ценность по 173-ФЗ. С 02.05.2027 резиденты отчитываются об операциях через некастодиальные кошельки. Тем, кто продаёт или покупает цифровую валюту, НПД применять нельзя.
- **Налоги.**
  - НПД: 6% с доходов от юрлиц, в том числе иностранных (чек с пометкой «Иностранная организация»), лимит 2,4 млн ₽ в год, эксперимент до 31.12.2028 ([npd.nalog.ru](https://npd.nalog.ru/faq/)).
  - НДФЛ для резидентов по прогрессивной шкале: 13% до 2,4 млн ₽, дальше 15/18/20/22%.
  - УСН освобождена от НДС при доходе до 20 млн ₽ (2025–2028 годы).
  - Рекламные услуги иностранному заказчику не облагаются российским НДС (ст. 148 НК).
  - КИК: доля больше 25% (или больше 10%, если у резидентов РФ вместе больше 50%) требует уведомления в течение 3 месяцев. Штрафы — 500 тыс. ₽ за КИК и 50 тыс. ₽ за участие. Прибыль КИК больше 10 млн ₽ облагается налогом, если нет освобождения (например, компания из ЕАЭС).

### 9.9 Российские площадки как основа или дополнение

| Площадка | Условия монетизации | Выплаты | Ограничения для автоконтента |
|---|---|---|---|
| **VK Видео** — партнёрская программа | С 01.09.2026: от 1000 подписчиков, 30 000 минут просмотра за 90 дней, хотя бы 1 видео за 180 дней, не меньше 3 горизонтальных видео от 3 минут, оригинальный контент ([t.me/vkvideo](https://t.me/vkvideo/7475)) | Revenue Share 50–80%; авторы получили >3 млрд ₽ за 2025 год | Перезаливы исключены условиями; VK удаляет до 250 тыс. единиц пиратского контента в месяц, получает ~15 тыс. обращений правообладателей |
| **VK Автор (Фонд оригинальных авторов)** | 1000–100 000 подписчиков; не меньше 2 клипов и 12 постов за 30 дней; подключён AdBlogger | 30 000 ₽/мес в течение 12 месяцев, за одно сообщество | Обязательно 16 единиц контента в месяц (4 клипа, 12 постов, 4 из них шопсы; исправлено); проверяются оригинальность и использование нейросетей и автоматизации |
| **VK AdBlogger** | От 100 подписчиков, резиденты РФ | Автор не платит комиссию; erid ставится автоматически; посевы удаляются через 72 часа, контент на заказ — через 30 дней | Правила модерации от 26.06.2026 не допускают площадки с «шаблонным, однотипным» контентом и рекламой без авторской составляющей; источник нужно указывать ссылкой |
| **Rutube** | 2 видео, 20 000 просмотров, резиденты от 14 лет со статусом самозанятого, ИП или юрлица ([rutube.ru](https://rutube.ru/info/monetization/)) | 1,5 млрд ₽ авторам за 2025 год; минимальная выплата 3000 ₽ | «Видеоряд ролика должен быть уникальным»; ролики, уже монетизируемые на других каналах, не монетизируются; монетизация Shorts анонсирована 01.09.2026 без даты и ставок; для агентств есть бизнес-аккаунт |
| **Yappy** | Вливается в Rutube как Rutube Shorts | — | Что Yappy «больше не существует», не подтверждено: yappy.media пока открывается как отдельный сервис |
| **Дзен** | 30 часов просмотра от подписчиков за 30 дней | «Вознаграждение начисляется только за текстовые форматы — статьи и посты» | За видео не платят с мая 2025 года |
| **Яндекс ПроБлогер** | От 1000 подписчиков, резиденты, самозанятые, ИП или юрлица; от 10 тыс. подписчиков нужен реестр РКН | Автопостинг в Telegram и MAX с оплатой за просмотры (пост удаляется через 48 часов); CPA на 6 площадках; минимальная выплата 3000 ₽ | Ставки не раскрыты; название «РСЯ для блогеров» не подтверждено |
| **Telegram** | Прямая продажа рекламы, посевы | Рынок ~90 млрд ₽ в год | Замедление с 10.02.2026, позиция ФАС, мораторий до 31.12.2026 |
| **MAX** | Реклама через Яндекс (бета с апреля 2026, ~2000 каналов), прямые интеграции | Оплата за действия | 77 млн DAU к марту 2026 — по данным статьи, не подтверждено |

---

## 10. Рекомендуемая архитектура системы

### 10.1 Принципы, которые следуют из разделов 2–9

1. Входной контент всегда имеет подтверждённое право на использование: собственный архив, договор с правообладателем, бриф кампании, CC BY/PD со сверкой, стоки как фон. Каждый исходник фиксируется в реестре прав.
2. В каждом ролике есть собственный вклад: комментарий, сюжет, разбор, голос или лицо ведущего, своя графика. Это закрывает reused/inauthentic на YouTube и оригинальность в Instagram и Facebook.
3. Шаблоны вариативны: разные сюжеты, разные хуки, серии с отдельной концепцией каждого выпуска. Это закрывает правило о «Generic or Repetitive Content».
4. Публикация идёт только через официальные API или аудированного посредника, с согласием владельца каждого канала.
5. Выручка диверсифицирована: интеграции с раскрытием, CPA, выплаты кампаний, VK и Rutube, YPP там, где он доступен по юрисдикции.
6. Маркировка встроена в пайплайн: erid, ЕРИР, раскрытие paid promotion и AI-метки (`containsSyntheticMedia`, `is_ai_generated`).

### 10.2 Схема пайплайна

```
┌─────────────────────────── ИСТОЧНИКИ ───────────────────────────┐
│ Свой архив │ Архив партнёра (договор) │ Брифы кампаний │ CC BY/PD │ Стоки (фон) │
└──────┬────────────────┬──────────────────┬─────────────┬────────┘
       ▼                ▼                  ▼             ▼
┌──────────────────── INGEST + РЕЕСТР ПРАВ (Postgres) ────────────┐
│ файл → S3/MinIO; лицензия, срок, территория, allowlist, заказчик │
│ проверка: без записи о праве исходник дальше не идёт            │
└──────────────────────────────┬──────────────────────────────────┘
                               ▼  очередь (Redis + RQ/ARQ/Celery)
┌──────────────────────── ПРЕДОБРАБОТКА ──────────────────────────┐
│ ffmpeg → аудио 16 кГц mono │ auto-editor (тишина) │ PySceneDetect │
└──────────────────────────────┬──────────────────────────────────┘
                               ▼
┌──────────────────────── ASR + ТАЙМКОДЫ ─────────────────────────┐
│ GigaAM-v3 (ONNX/CPU) или WhisperX (GPU) │ облако: SpeechKit       │
│ выход: текст + word timestamps (+ диаризация pyannote на GPU)   │
└──────────────────────────────┬──────────────────────────────────┘
                               ▼
┌──────────────────────── ВЫБОР МОМЕНТОВ ─────────────────────────┐
│ LLM (Gemini Flash-Lite / Ollama) → JSON: start, end, хук, тема   │
│ фильтр по конкретным метрикам удержания своих прошлых роликов    │
└──────────────────────────────┬──────────────────────────────────┘
                               ▼
┌──────────────── РЕДАКЦИОННЫЙ СЛОЙ (человек в контуре) ──────────┐
│ сценарий комментария/разбора │ запись голоса/лица ведущего       │
│ или TTS (CosyVoice3 / Chatterbox / SpeechKit) + своя графика     │
└──────────────────────────────┬──────────────────────────────────┘
                               ▼
┌──────────────────────── МОНТАЖ И РЕНДЕР ────────────────────────┐
│ reframe 9:16: MediaPipe/YOLO + Light-ASD, сглаживание EMA        │
│ субтитры ASS→libass │ интеграция спонсора (авторский сегмент)    │
│ erid/«Реклама» для РФ │ NVENC или libx264 veryfast               │
└──────────────────────────────┬──────────────────────────────────┘
                               ▼
┌──────────────────────── QA / COMPLIANCE ────────────────────────┐
│ чек-лист: право есть? вклад есть? не дубль своей же библиотеки?  │
│ раскрытие paid promotion / AI? категория рекламодателя разрешена?│
└──────────────────────────────┬──────────────────────────────────┘
                               ▼
┌──────────────────────── ПУБЛИКАЦИЯ ─────────────────────────────┐
│ n8n-оркестрация → YouTube Data API (аудированный проект)         │
│ или Upload-Post/Zernio │ IG Content Publishing API │ VK/Rutube    │
│ планировщик: лимиты, токены, окна публикации, Trial Reels        │
└──────────────────────────────┬──────────────────────────────────┘
                               ▼
┌──────────────────────── АНАЛИТИКА ──────────────────────────────┐
│ YouTube Analytics/Reporting API (engagedViews, CTR, удержание)   │
│ IG Insights (views, avg watch time, skip rate, sends)            │
│ UTM + промокоды → CRM │ ОРД-статистика → ЕРИР до 30-го числа      │
│ Account Status / статус YPP / заявки Content ID → алерты         │
└──────────────────────────────┬──────────────────────────────────┘
                               └──► обратная связь в «Выбор моментов»
```

### 10.3 Компоненты

| Слой | Инструмент | Комментарий |
|---|---|---|
| Хранилище | S3-совместимое (MinIO или облако), Postgres | Реестр прав, метаданные клипов, статусы публикаций; отчёты Reporting API выгружать до истечения 60 дней |
| Очереди | Redis + RQ, ARQ или Celery | Отдельные очереди для ASR, рендера и публикации; повторы при ошибках квоты |
| Оркестрация | n8n (fair-code, self-host) | Только публикация и уведомления; тяжёлая обработка — в Python-воркерах |
| ASR | GigaAM-v3; WhisperX на GPU; SpeechKit | Замерить на 1 часе собственного подкаста |
| LLM | Gemini Flash-Lite (не из РФ) / Ollama | Строгая JSON-схема, валидация границ фрагментов |
| Рендер | ffmpeg (overlay, drawtext, libass), опционально Remotion | Remotion для команды от 4 человек стоит минимум $100 в месяц |
| Публикация | YouTube Data API (аудит), IG API, Upload-Post или Zernio для TikTok | Мониторинг `content_publishing_limit`, срока жизни токенов, `uploadLimitExceeded` |
| Мониторинг | Алерты в Telegram или почту | Статус YPP, Account Status, новые заявки Content ID, срок жизни токенов |

### 10.4 Железо

- **CPU-воркер:** 8 vCPU, 16 ГБ RAM, x86_64 (на ARM WhisperX работает только на CPU). Час исходника обрабатывается за ~15–30 минут с GigaAM (оценка).
- **GPU по запросу:** RTX 4090 на RunPod — $0,34/ч в Community, $0,74/ч в Secure. Час исходника — ~5–8 минут (оценка). Круглосуточно: ~$248/мес в Community, ~$540/мес в Secure.
- Тяжёлую обработку держать на отдельном воркере, не на боевом сервере приложения.

### 10.5 Стоимость в месяц (оценка)

| Статья | MVP: ~100 ч исходника, ~1000 клипов, 3–5 каналов | Масштаб: ~1000 ч, ~10 000 клипов, 50–75 профилей |
|---|---|---|
| GPU (аренда по часам) | ~$5–30 | ~$50–250 |
| CPU-воркер / VPS | ~$40–80 (оценка рынка, в исследовании не проверялась) | ~$80–200 |
| ASR в облаке (если без GPU) | ~900 ₽ (SpeechKit в отложенном режиме) | ~9 000 ₽ |
| LLM для выбора моментов | $1–5 | $10–50 |
| TTS (если нужен) | $0–20 | $20–200 |
| Публикация | Upload-Post $24–50 или собственные API ($0) | Upload-Post $147 / Zernio ~$200–250 (оценка по тарифной сетке) |
| Музыка | $0 (Audio Library, Sound Collection) или Epidemic €11–17 | Epidemic Pro Plus/Enterprise или бесплатные библиотеки |
| Хранилище | $5–20 | $30–100 |
| ОРД, маркировка (РФ) | По тарифу оператора | По тарифу оператора |
| **Инфраструктура итого** | **~$100–250** | **~$500–1100** |
| Люди (редактор, ведущий голосом или в кадре, менеджер продаж) | Главная статья расходов, в исследовании не оценивалась | — |

Для сравнения: SaaS-нарезка при 100 часах исходника стоит $300–1500 в месяц ($3–15 за час).

### 10.6 Этапы запуска

**Неделя 0 — решения** (раздел 12): юрисдикция получателя денег, целевой язык и страны аудитории, площадки, источники контента, модель выручки.

**Недели 1–2 — MVP:**
- реестр прав, ingest, ASR (замер GigaAM против WhisperX), выбор моментов, reframe, ASS-субтитры, ручная QA;
- заявка на аудит YouTube API и верификацию OAuth (официально 3–5 рабочих дней), либо сразу Upload-Post;
- Instagram API на своих бизнес-аккаунтах через Standard Access;
- для РФ-направления: регистрация самозанятого или ИП, договор с ОРД, подключение VK AdBlogger / Яндекс ПроБлогер.

**Недели 3–4 — пилот:**
- 3–5 каналов, 1–3 ролика в день на канал;
- Trial Reels для тестов форматов;
- дашборд: engaged views, средняя длительность просмотра, skip rate, CTR обложек, переходы по UTM и промокодам;
- первые интеграции или CPA-офферы;
- еженедельная проверка Account Status, статуса YPP и заявок.

**Месяцы 2–3 — масштаб:**
- расширение числа источников по договорам;
- Light-ASD для подкастов;
- автодубляж или multi-language audio (для английского исходника — 20 языков);
- доля Shorts 25–40% от загрузок при наличии длинных видео;
- пакетная продажа интеграций на сеть; при каждом размещении раскрытие делается в каждом ролике.

**Месяц 4 и дальше:** подготовка к порогам YPP 2027 года (10 млн квалифицированных просмотров Shorts за 90 дней на канал), продажа интеграций, dynamic brand segments при приглашении, B2B-модель «контент-завода» для брендов (CPV ~0,1 ₽ в РФ).

---

## 11. Риски и как их закрыть

| Риск | Вероятность / влияние | Как закрыть |
|---|---|---|
| Заявки Content ID и страйки на чужой контент | Высокая при чужом контенте / потеря дохода, удаление аккаунта при 3 страйках за 90 дней | Только контент с подтверждённым правом; allowlist от правообладателя; реестр прав |
| Отказ или исключение из YPP за reused / inauthentic | Высокая при нарезках без вклада / демонетизация всего канала и связанных каналов | Редакционный слой (комментарий, сюжет, голос), вариативные шаблоны, человек в контуре; разный контент на разных каналах |
| Спам-политика (detection evasion, scraped, mass-production) | Высокая при «уникализации» / удаление всех каналов владельца | Не использовать технические модификации ради обхода детекции; частота публикаций в пределах, которые держит редакция |
| Сетевой риск (related channel, circumvention) | Средняя–высокая / потеря сети целиком | Все каналы строятся на одинаково чистом контенте; нарушающих экспериментов нет ни на одном канале владельца |
| Instagram: аккаунт убран из рекомендаций | Высокая у агрегаторов / охват только по подписчикам | Большинство постов оригинальные за скользящие 30 дней; Collab и Remix с правообладателем; мониторинг Account Status |
| Отсутствие YPP и AdSense для РФ | Определено / нет рекламной выручки YouTube | Доход через интеграции, CPA, VK и Rutube; при реальном проживании в стране YPP — отдельная юридическая проработка |
| Запрет рекламы в Instagram (РФ) и позиция ФАС по YouTube и Telegram после 31.12.2026 | Определено / штрафы до 500 тыс. ₽ юрлицу | Для российских рекламодателей — VK, Rutube, MAX, Дзен; мониторинг решений ФАС в декабре 2026 года |
| Маркировка, ЕРИР, сбор 3% | Определено / штрафы до 500 тыс. ₽ | ОРД с первого размещения, erid в пайплайне рендера, календарь платежей по сбору |
| Реестр РКН для страниц 10 тыс.+ | Определено / запрет рекламы и донатов | Регистрация каждой страницы в течение 10 рабочих дней после перехода порога |
| Гемблинг и крипта в рекламе | Высокая в клиппинг-нише / нарушение закона и правил площадок | Стоп-лист категорий в CRM рекламодателей; для крипты — только по ст. 29.2 |
| Порог 10 млн просмотров Shorts с 01.02.2027 | Высокая для мелких каналов / нет дохода из пула | Меньше каналов с большим охватом; упор на длинные видео и интеграции; принять новые условия до 31.01.2027 |
| API: private-блокировка, квоты, токены | Высокая без аудита / публикации невидимы | Аудит проекта или аудированный посредник; мониторинг квоты (≤95–98 роликов в сутки с обложкой и плейлистом), продление токенов |
| Зависимость от посредника публикации | Средняя / все каналы встают разом | Резервный путь (собственный аудированный проект), экспорт расписаний |
| Неофициальная автоматизация (instagrapi, эмуляция устройств) | Высокая / баны аккаунтов | Только официальные API |
| AI-метки и likeness detection | Растёт / удаление роликов с реальными людьми | Раскрытие синтетики; без реалистичных изображений реальных людей без их согласия |
| Лицензии ПО (AGPL, Remotion, некоммерческие TTS) | Средняя / претензии, переписывание стека | MIT/Apache/BSD в ядре; Ultralytics Enterprise или MediaPipe; TTS только с коммерческой лицензией |
| Вредоносные репозитории | Низкая–средняя / утечка токенов | Не запускать непроверенный код рядом с ключами; изоляция воркеров |
| Платежи и валютный контроль | Определено для РФ / блокировка выплат, штрафы 20–40% | Счета в ЕАЭС или странах обмена; уведомления в ФНС; НПД или ИП; без криптовыплат до ясности по 283-ФЗ |
| Ложные срабатывания модерации | Средняя / временная потеря монетизации | Документировать процесс создания (скринкаст монтажа помогал восстановить монетизацию за 1,5 дня), архив исходников и договоров |
| Задержки и отказы клиппинг-кампаний | Средняя / кассовые разрывы | Несколько кампаний параллельно, учёт одобренных роликов, регионы аудитории под условия кампании |

---

## 12. Открытые вопросы, которые нужно решить клиенту

**Решения по бизнесу:**
1. **Юрисдикция получателя денег.** Реальное проживание в стране YPP (Казахстан, Армения, Грузия и др.) или модель без YPP (интеграции, CPA, VK, Rutube)? От этого зависит, доступна ли вообще рекламная выручка YouTube.
2. **Источник контента.** Собственное производство, договоры с конкретными авторами (кто они, на каких условиях — фикс, процент, эксклюзив), клиппинг-кампании или AI-генерация? Нужен список первых 3–5 правообладателей.
3. **Язык и география аудитории.** Русский (VK и Rutube в РФ, YouTube без рекламной выручки из РФ) или английский и европейские языки (выше RPM, автодубляж в 20 языков, но насыщенный рынок)?
4. **Площадки.** YouTube (длинные видео + Shorts), Instagram (только трафик и брендинг), Facebook (Content Monetization по приглашению), VK Видео, Rutube, Telegram/MAX?
5. **Модель выручки и цели.** Доля интеграций, CPA, выплат кампаний, YPP. Целевой доход в месяц и допустимый срок выхода на него.
6. **Редакционный слой.** Кто даёт собственный вклад — ведущий в кадре, голос, сценарист? Сколько роликов в день он реально выдерживает?
7. **Юрлицо и налоговый режим.** Самозанятость (лимит 2,4 млн ₽), ИП на УСН, иностранная компания (КИК)?
8. **Стоп-лист рекламодателей.** Исключить ли гемблинг, кейс-сайты и крипту, где сосредоточена часть клиппинг-бюджетов?
9. **Бюджет на людей и инфраструктуру** на первые 3 месяца.

**Вопросы, которые исследование не закрыло (нужна проверка на практике):**
- Как на деле работает изменение от 24.09.2026 для Shorts длиной 1–3 минуты с заявкой Content ID и кому идёт доход.
- Как проверяющие YPP оценивают лицензированные нарезки подкастов и стримов с allowlist. Официальной границы «достаточной» переработки нет, отчётов об успешной рекламной монетизации таких каналов не найдено.
- Реальный RPM русскоязычного контента вне РФ (Казахстан, Израиль, Германия, Балтия). Нужен тестовый канал.
- Фактический лимит Instagram API (50 или 100) — проверить через `content_publishing_limit` на своём аккаунте.
- Решение ФАС по рекламе в YouTube и Telegram с 01.01.2027.
- Сроки и ставки монетизации Rutube Shorts; реальный CPM VK Видео; монетизируются ли VK Клипы в партнёрской программе.
- Засчитываются ли часы дублированных дорожек в пороги YPP (прямого подтверждения нет).
- Продадут ли Newsflare, Epidemic Sound и аналоги лицензию с оплатой из РФ.
- Выдаёт ли YouTube квоту больше 100 загрузок в сутки на проект сценарию «автопубликация в собственные каналы».
- Доступны ли из РФ Hugging Face gated-модели (pyannote) и RunPod; есть ли российские GPU-облака сопоставимой цены.
- Точные ставки CPM и CPA у Яндекс ПроБлогера и доля автора в аукционе.

---

## 13. Источники

### YouTube: политики и справка
- [YPP: правила монетизации каналов (reused, inauthentic, три категории)](https://support.google.com/youtube/answer/1311392?hl=en)
- [Условия YPP и пороги](https://support.google.com/youtube/answer/72851?hl=en) · [Изменения YPP 2027](https://support.google.com/youtube/answer/12843009?hl=en) · [Блог: YPP 2027](https://blog.youtube/news-and-events/youtube-partner-program-updates-2027-new-opportunities-earn/) · [Блог: qualified watch hours](https://blog.youtube/news-and-events/youtube-monetization-qualified-watch-hours-shorts-views/)
- [Монетизация Shorts](https://support.google.com/youtube/answer/12504220?hl=en) · [Трёхминутные Shorts и Content ID](https://support.google.com/youtube/answer/15424877?hl=en)
- [Content ID](https://support.google.com/youtube/answer/2797370?hl=en) · [Заявки Content ID](https://support.google.com/youtube/answer/6013276?hl=en) · [Допуск к Content ID](https://support.google.com/youtube/answer/1311402?hl=en) · [Allowlist](https://support.google.com/youtube/answer/6070344?hl=en) · [Страйки](https://support.google.com/youtube/answer/2814000?hl=en) · [Fair use](https://support.google.com/youtube/answer/9783148?hl=en)
- [Спам-политика](https://support.google.com/youtube/answer/2801973?hl=en) · [Удаление каналов](https://support.google.com/youtube/answer/2802168?hl=en) · [Circumvention](https://support.google.com/youtube/answer/2801981?hl=en) · [Second chance](https://blog.youtube/inside-youtube/second-chances-on-youtube/) · [TeamYouTube об удалениях, 13.11.2025](https://support.google.com/youtube/thread/387431861?hl=en)
- [Раскрытие ИИ](https://support.google.com/youtube/answer/14328491?hl=en) · [Branded content](https://support.google.com/youtube/answer/17596007?hl=en) · [Paid promotion](https://support.google.com/youtube/answer/154235?hl=en) · [Запрет сторонних рекламных вставок](https://support.google.com/youtube/answer/3364658?hl=en) · [Dynamic brand segments](https://support.google.com/youtube/answer/17415761?hl=en) · [Creator Partnerships](https://support.google.com/youtube/answer/9385307?hl=en) · [Open Call](https://support.google.com/youtube/answer/17210205?hl=en)
- [Ссылки в Shorts](https://support.google.com/youtube/answer/13748639?hl=en) · [Edit into a Short](https://support.google.com/youtube/answer/12836917) · [Turn audio into Short](https://support.google.com/youtube/answer/15260303) · [Related video, гайд](https://blog.youtube/creator-and-artist-stories/youtube-related-videos-traffic-guide/) · [Мифы о Shorts](https://blog.youtube/creator-and-artist-stories/debunking-common-myths-about-youtube-shorts/)
- [Страны YPP](https://support.google.com/youtube/answer/7101720?hl=en) · [MCN](https://support.google.com/youtube/answer/2737059) · [Автодубляж](https://support.google.com/youtube/answer/15569972?hl=en) · [Multi-language audio](https://blog.youtube/news-and-events/multi-language-audio/)
- [Письмо CEO, 2026](https://blog.youtube/inside-youtube/the-future-of-youtube-2026/) · [Made on YouTube 2026](https://blog.youtube/news-and-events/innovation-youtube-era-made-on-viewers-creators/) · [Условия использования YouTube](https://www.youtube.com/t/terms)

### Meta / Instagram / Facebook
- [Rewarding original creators (30.04.2026)](https://creators.instagram.com/blog/rewarding-original-creators-on-instagram) · [Original Content Guidelines](https://creators.instagram.com/original-content-guidelines) · [Recommendations and originality (2024)](https://creators.instagram.com/recommendations-and-originality)
- [Recommendations Guidelines](https://help.instagram.com/313829416281232/) · [Account Status](https://help.instagram.com/653964212890722) · [Авторское право](https://help.instagram.com/354736791367645/) · [Content Monetization Policies](https://help.instagram.com/2635536099905516) · [AI-generated profile](https://creators.instagram.com/blog/ai-generated-profile-label)
- [Facebook: combating unoriginal content](https://creators.facebook.com/blog/combating-unoriginal-content) · [Rewarding original creators on Facebook (03.2026)](https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/) · [Спам-сети (04.2025)](https://about.fb.com/news/2025/04/cracking-down-spammy-content-facebook/) · [Content protection](https://creators.facebook.com/blog/content-protection-your-new-creative-guardian-on-facebook) · [Rights Manager](https://www.facebook.com/business/help/1548693938521733) · [Transparency: IP](https://transparency.meta.com/reports/intellectual-property/protecting-intellectual-property-rights/)
- [Branded Content Policies](https://www.facebook.com/business/help/221149188908254) · [Определение баннера](https://www.facebook.com/business/help/1190980254246452) · [Creator Marketplace](https://help.instagram.com/1389278101788752) · [Creator Fast Track](https://about.fb.com/news/2026/03/creator-fast-track-grow-your-audience-earn-money-on-facebook/) · [Gifts](https://help.instagram.com/738469380549477)

### API и сервисы публикации
- [YouTube Data API: videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert) · [Квоты](https://developers.google.com/youtube/v3/determine_quota_cost) · [Revision history](https://developers.google.com/youtube/v3/revision_history) · [Developer Policies](https://developers.google.com/youtube/terms/developer-policies) · [Аудит](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits) · [Locked as private](https://support.google.com/youtube/answer/7300965?hl=en) · [OAuth](https://developers.google.com/identity/protocols/oauth2) · [Analytics revision history](https://developers.google.com/youtube/analytics/revision_history)
- [Instagram Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/) · [IG User Media](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media) · [Access Levels](https://developers.facebook.com/docs/graph-api/overview/access-levels/) · [Разбор лимитов (bundle.social)](https://bundle.social/blog/instagram-api-rate-limits)
- [TikTok Content Posting API](https://developers.tiktok.com/doc/content-posting-api-get-started) · [Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) · [TikTok Branded Content Policy](https://www.tiktok.com/legal/page/global/bc-policy/en)
- [Upload-Post](https://www.upload-post.com/llms-full.txt) · [Zernio](https://zernio.com/pricing) · [Blotato](https://www.blotato.com/pricing) · [Ayrshare](https://www.ayrshare.com/pricing/) · [Postiz](https://github.com/gitroomhq/postiz-app) · [Mixpost](https://mixpost.app/pricing) · [SMMplanner](https://smmplanner.com/) · [Postmypost](https://postmypost.io/ru/prices) · [instagrapi](https://github.com/subzeroid/instagrapi)

### Технический стек
- [openshorts](https://github.com/mutonby/openshorts) · [AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) · [supoclip](https://github.com/FujiwaraChoki/supoclip) · [opensource-clipping](https://github.com/NaufalRizqullah/opensource-clipping) · [podcli](https://github.com/nmbrthirteen/podcli)
- [GigaAM](https://github.com/salute-developers/GigaAM) · [GigaAM-v3 на HF](https://huggingface.co/ai-sage/GigaAM-v3) · [stt-ru-benchmark](https://github.com/berdachuk/stt-ru-benchmark) · [faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [WhisperX](https://github.com/m-bain/whisperX) · [whisper.cpp](https://github.com/ggml-org/whisper.cpp) · [pyannote](https://github.com/pyannote/pyannote-audio)
- [MediaPipe](https://github.com/google-ai-edge/mediapipe) · [Ultralytics](https://github.com/ultralytics/ultralytics) · [Light-ASD](https://github.com/Junhua-Liao/Light-ASD) · [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) · [auto-editor](https://github.com/WyattBlue/auto-editor) · [Remotion: лицензия](https://www.remotion.pro/license) · [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html)
- [OpusClip](https://www.opus.pro/pricing) · [Vizard](https://docs.vizard.ai/docs/pricing) · [Klap](https://docs.klap.app/pricing) · [Submagic](https://www.submagic.co/pricing) · [Mirage](https://captions.ai/help/docs/api/pricing)
- [Yandex SpeechKit: цены](https://aistudio.yandex.ru/docs/ru/speechkit/pricing.html) · [AssemblyAI: языки](https://www.assemblyai.com/docs/pre-recorded-audio/supported-languages) · [Gemini API: цены](https://ai.google.dev/gemini-api/docs/pricing) · [RunPod](https://www.runpod.io/pricing)

### AI-генерация и TTS
- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) · [MoneyPrinterV2](https://github.com/FujiwaraChoki/MoneyPrinterV2) · [RedditVideoMakerBot](https://github.com/elebumm/RedditVideoMakerBot) · [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms) · [n8n #5338](https://n8n.io/workflows/5338)
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) · [Chatterbox](https://github.com/resemble-ai/chatterbox) · [Silero](https://github.com/snakers4/silero-models) · [edge-tts](https://github.com/rany2/edge-tts) · [ElevenLabs API](https://elevenlabs.io/pricing/api) · [SaluteSpeech](https://developers.sber.ru/docs/ru/salutespeech/tariffs/legal-tariffs)
- [Veo](https://ai.google.dev/gemini-api/docs/veo) · [Runway API: цены](https://docs.dev.runwayml.com/guides/pricing/) · [Kling v3 Pro на fal](https://fal.ai/models/fal-ai/kling-video/v3/pro/text-to-video) · [Sora 2 (закрыт)](https://developers.openai.com/api/docs/models/sora-2) · [Wan 2.2](https://github.com/Wan-Video/Wan2.2) · [HunyuanVideo 1.5](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5) · [LTX-2](https://github.com/Lightricks/LTX-2)

### Клиппинг, лицензии, стоки, музыка
- [Whop Content Rewards (docs)](https://docs.whop.com/memberships-and-access/third-party-apps/content-rewards.md) · [Whop blog](https://whop.com/blog/content-rewards/) · [Whop: KYC по странам](https://docs.whop.com/developer/verification/identity-documents.md) · [Clipping.net](https://clipping.net) · [Vyro](https://vyro.com/) · [Content Rewards](https://www.contentrewards.com/terms) · [Klipni](https://klipni.com/) · [OpenClip: гайд](https://openclip.app/guides/whop-clipping-guide)
- [Newsflare](https://www.newsflare.com/pricing) · [ViralHog](https://viralhog.com/) · [Storyful](https://storyful.com/) · [Twitch ToS](https://legal.twitch.com/en/legal/terms-of-service/) · [Twitch Clips](https://help.twitch.tv/s/article/how-to-use-clips?language=en_US)
- [Pexels](https://www.pexels.com/license/) · [Pixabay](https://pixabay.com/service/license-summary/) · [Mixkit](https://mixkit.co/license/) · [Coverr](https://coverr.co/license) · [Magnific](https://www.magnific.com/legal/terms-of-use) · [NASA](https://www.nasa.gov/nasa-brand-center/images-and-media/) · [Prelinger](https://archive.org/details/prelinger)
- [YouTube Audio Library](https://support.google.com/youtube/answer/3376882?hl=en) · [Meta Sound Collection](https://help.instagram.com/402084904469945) · [Epidemic Sound](https://www.epidemicsound.com/pricing/)

### Рынок, исследования, отчёты практиков (EN)
- [TorrentFreak: Content ID 2025](https://torrentfreak.com/youtube-processed-2-5-billion-content-id-copyright-claims-in-2025/) · [Kapwing: AI slop report](https://www.kapwing.com/blog/ai-slop-report-the-global-rise-of-low-quality-ai-videos/) · [XDA: удаление AI-slop](https://www.xda-developers.com/youtube-just-deleted-over-4-7-billion-views-worth-ofai-slop-videos/) · [Tubefilter: удаления 01.2026](https://www.tubefilter.com/2026/01/29/youtube-ai-slop-channel-crackdown-bans/) · [TheWrap: Screen Culture](https://www.thewrap.com/industry-news/tech/youtube-terminates-screen-culture-kh-studio-over-ai-fake-movie-trailers/)
- [Tubefilter: июль 2025](https://www.tubefilter.com/2025/07/03/youtube-crackdown-repetitive-non-original-content/) · [Tubefilter: июль 2026](https://www.tubefilter.com/2026/07/13/youtube-inauthentic-content-monetization-policy-update/) · [TechCrunch: июль 2026](https://techcrunch.com/2026/07/20/youtube-clarifies-policies-around-ai-slop-and-upsetting-videos/) · [Google Research: S-CTS](https://research.google/pubs/scalable-detection-of-adversarial-synthetic-slop-and-coordinated-media-abuse-a-lora-enabled-multimodal-defense-system/) · [Dexerto: 12 млн каналов](https://www.dexerto.com/youtube/youtube-responds-to-ai-concerns-as-12-million-channels-terminated-in-2025-3292911/)
- [NPR: клип-экономика](https://www.npr.org/2026/05/12/nx-s1-5794670/influencers-creators-video-clips) · [Prof G: The Clip Economy](https://www.profgmedia.com/p/the-clip-economy) · [TubeBuddy: сколько платят Shorts](https://www.tubebuddy.com/blog/youtube-shorts-pay-1000000-views/) · [Mediacube: RPM Shorts](https://mediacube.io/en-US/blog/youtube-shorts-rpm) · [AIR: RPM по нишам](https://air.io/en/air-data-findings/which-youtube-niche-makes-the-most-money-in-2026-ranked-by-real-rpm-and-cpm) · [AIR: RPM Shorts](https://air.io/en/monetization/what-rpm-can-you-expect-from-shorts-in-2026) · [AIR: Shorts и длинные видео](https://air.io/en/audience-growth/do-youtube-shorts-help-your-long-form-videos-grow-data-from-18000-channels)
- [Influencer Marketing Hub: ставки](https://influencermarketinghub.com/influencer-rates/) · [ThoughtLeaders: Shorts-спонсорство](https://www.thoughtleaders.io/blog/youtube-shorts-sponsorships-brands-investing) · [Passionfroot](https://www.passionfroot.me/creator-pricing) · [Sotrender](https://www.sotrender.com/blog/2026/08/instagram-reach-is-down-what-the-data-actually-says/) · [Keywords Everywhere](https://keywordseverywhere.com/news/instagram-algorithm-updates/) · [Oversight Board (TechCrunch)](https://techcrunch.com/2026/06/04/metas-oversight-board-says-account-bans-lack-due-process-transparency/)
- Reddit: [inauthentic, 300M+](https://www.reddit.com/r/PartneredYoutube/comments/1sdvbo5/any_luck_dealing_with_inauthentic_content_notice/) · [смена ярлыка, ответ Rene Ritchie](https://www.reddit.com/r/PartneredYoutube/comments/1uwkc7g/first_it_was_inauthentic_content_now_suddenly/) · [related channel](https://www.reddit.com/r/PartneredYoutube/comments/1vrotg1/does_reapplication_rejection_also_trigger_related/) · [1M+ сеть удалена](https://www.reddit.com/r/PartneredYoutube/comments/1vf67ju/youtube_terminated_my_channels_1m_subscribers/) · [RPM $0,04](https://www.reddit.com/r/PartneredYoutube/comments/1oau67h/my_short_hit_1m_views_in_2_days_but_only_earned/) · [клиппинг $2372](https://www.reddit.com/r/passive_income/comments/1tgljtu/hit_2372_in_my_first_month_of_clipping_is_this/)

### Россия: практика и площадки
- [vc.ru: AdSense и РФ](https://vc.ru/3089463) · [Habr: отключение AdSense](https://habr.com/ru/articles/836518/) · [searchengines.guru: каскадное удаление](https://searchengines.guru/ru/forum/1133987) · [searchengines.guru: нарезки фильмов](https://searchengines.guru/ru/forum/1103844) · [vc.ru: кейсы монетизации](https://vc.ru/2840314) · [vc.ru: УБТ с Shorts](https://vc.ru/1761968) · [cpa.rip](https://cpa.rip/youtube/youtube-shorts-traffic-monetizing/)
- [vc.ru: клиппинг стримов](https://vc.ru/2918998) · [vc.ru: Musor Drop/Upgrader](https://vc.ru/3108788) · [vc.ru: контент-заводы](https://vc.ru/3017932)
- [VK Видео: условия партнёрской программы](https://t.me/vkvideo/7475) · [vk.ru/monetization](https://vk.ru/monetization) · [VK AdBlogger](https://adblogger.vk.ru/for-authors) · [AdBlogger: модерация](https://adblogger.vk.ru/documents/groups_moderation) · [Кейс Ukids](https://adblogger.vk.ru/top-cases/ukids) · [Кейс Drivee](https://adblogger.vk.ru/top-cases/drivee)
- [Rutube: монетизация](https://rutube.ru/info/monetization/) · [Rutube: правила рекламы](https://rutube.ru/info/adverguide/) · [Дзен: условия](https://dzen.ru/help/ru/monetization/conditions.html) · [Яндекс ПроБлогер](https://partner.yandex.ru/messengers) · [Яндекс Директ: Telegram](https://yandex.ru/project/direct/performance/promo_telegram/direct_tg_reach_and_perf)
- [Mediascope: VK Видео и YouTube](https://yagla.ru/blog/socialnye-seti/mediascope-vk-video-obognal-youtube-po-ejednevnoy-auditorii-v-rossii-v-yanvare--2602u106040/) · [Коммерсант: аудитория](https://www.kommersant.ru/doc/8798352) · [Carnegie: блокировка YouTube](https://carnegieendowment.org/russia-eurasia/politika/2025/02/russia-youtube-block-attempt?lang=en) · [DW: блокировка 02.2026](https://www.dw.com/en/youtube-whatsapp-blocked-in-russia/a-75940102)

### Право: РФ, США, ЕС
- [38-ФЗ, ст. 5](https://www.consultant.ru/document/cons_doc_LAW_58968/f67f81c57fdcdacc2643d19d59369f7e185e1156/) · [ст. 18.1](https://www.consultant.ru/document/cons_doc_LAW_58968/2c4537e4796f6ff8b2736ed1b0d4fef08e14458e/) · [ст. 18.2](https://www.consultant.ru/document/cons_doc_LAW_58968/f5499f6a99f4f1011e473eae3412f6c7f0262387/) · [ст. 27](https://www.consultant.ru/document/cons_doc_LAW_58968/542c1d26e7bf4493d8f54c8ea2edf498dd68e6b2/) · [ст. 29.2](https://www.consultant.ru/document/cons_doc_LAW_58968/c667f7204e9956c4e9cc0ccd7eb6765eb5dc2d7e/) · [72-ФЗ](http://publication.pravo.gov.ru/document/0001202504070018)
- [КоАП ст. 14.3](https://www.consultant.ru/document/cons_doc_LAW_34661/2d50fc1c4013ea9ab20b8b2666c1650b1dc4c982/) · [КоАП ст. 15.25](https://www.consultant.ru/document/cons_doc_LAW_34661/4adb77015309c711961ab12777e722a3b004b694/) · [173-ФЗ ст. 12](https://www.consultant.ru/document/cons_doc_LAW_45458/15452676240afa2b4ce846308d8f9434eaac4019/) · [149-ФЗ ст. 10.6](https://www.consultant.ru/document/cons_doc_LAW_61798/a3cba9a7c2ac9aa487df2d4172734dd5139376f5/) · [422-ФЗ (НПД)](https://www.consultant.ru/document/cons_doc_LAW_311977/)
- [ГК ст. 1270](https://www.zakonrf.info/gk/1270/) · [ГК ст. 1274](https://www.consultant.ru/document/cons_doc_LAW_64629/84bbd636598a59112a4fe972432343dd4f51da1d/) · [ГК ст. 1301](https://www.consultant.ru/document/cons_doc_LAW_64629/c2f79b53ce582e92680379e2ebd23eeb9fb7855a/) · [УК ст. 146](https://www.consultant.ru/document/cons_doc_LAW_10699/b683408102681707f2702cff05f0a3025daab7ab/) · Дела: [А53-5015/2025](https://sudact.ru/arbitral/doc/doNZtLtRzYH4/), [А48-11027/2024](https://sudact.ru/arbitral/doc/itrcxdhqVt1S/), [А39-6371/2025](https://sudact.ru/arbitral/doc/diLaGGB8FKdE/), [А40-305533/2024](https://sudact.ru/arbitral/doc/7wTsRwCi87Ws/)
- [ФАС: мораторий (rb.ru)](https://www.rb.ru/news/fas-dala-oficialnoe-razyasnenie-za-reklamu-v-telegram-i-youtube-ne-budut-shtrafovat-do-2027-goda/) · [iStories: ФАС и YouTube](https://istories.media/news/2026/03/06/sledom-za-telegramom-fas-i-rkn-nazvali-reklamu-na-youtube-zapreshchennoi/) · [AdIndex: первое дело по Instagram](https://adindex.ru/news/right/2025/10/28/338668.phtml) · [PGPLaw: порядок уплаты 3%](https://www.pgplaw.ru/analytics-and-brochures/alerts/pravitelstvo-utverdilo-poryadok-uplaty-obyazatelnykh-otchisleniy-za-reklamu-v-internete-/) · [ОРД-А](https://ord-a.ru/wiki/key_points/)
- [US Copyright Office: fair use](https://www.copyright.gov/fair-use/) · [Hosseinzadeh v. Klein](https://www.copyright.gov/fair-use/summaries/hosseinzadeh-klein-sdny2017.pdf) · [17 U.S.C. §512](https://www.law.cornell.edu/uscode/text/17/512) · [USCO: AI Part 2](https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf) · [Thaler v. Perlmutter](https://media.cadc.uscourts.gov/opinions/docs/2025/03/23-5233.pdf) · [Директива DSM 2019/790](https://eur-lex.europa.eu/eli/dir/2019/790/oj/eng) · [IRS: соглашение с РФ](https://www.irs.gov/businesses/international-businesses/russia-tax-treaty-documents) · [OFAC: Russia](https://ofac.treasury.gov/sanctions-programs-and-country-information/russian-harmful-foreign-activities-sanctions)
