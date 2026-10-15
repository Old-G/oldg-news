-- Manual tuning of Telegram sources, from the read of the notes channel (docs/taste_baseline.md).
-- Idempotent; run after the first fetch has registered the channels:
--   docker compose exec -T postgres psql -U radar -d radar -f - < db/sources_tuning.sql
-- fetch never overwrites weight or enabled, so the tuning survives later runs.

-- Noise: never saved to notes
update sources set enabled = false where kind = 'telegram' and external_id in (
  '-1001395363861',  -- Proxy MTProto
  '-1001654483967',  -- CY IT HR Vacancy
  '-1001447304363',  -- Jobs Code: IT вакансии
  '-1001576639361',  -- TON Jobs
  '-1002406974443',  -- Вакансии в IT | By Ulbi TV
  '-1001294525023',  -- Работа&вакансии на Кипре
  '-1001168930792',  -- astro geeks | гороскоп и таро
  '-1001669505922',  -- tarot geeks | таро на день
  '-1002053076835',  -- Гороскоп Лошади
  '-1002426804950',  -- Таро Гадание с помощью ИИ
  '-1001819816449',  -- Телец Таро гороскоп
  '-1001104702631',  -- Luxurytrade_minsk Apple/Dyson/Ps5
  '-1001300421671',  -- Прайс Apple Luxury_trademinsk
  '-1001364412069',  -- CoinMarketCap Announcements
  '-1001319585796',  -- CoinMarketCap: New Listings Notifications
  '-1001591872543',  -- NFTime Announcements
  '-1001861308806',  -- Nifty NFT
  '-1001821068055'   -- Unibot Entry Portal
);

-- Crypto / trading: kept for AI-in-markets stories, but low trust
update sources set weight = 0.5 where kind = 'telegram' and external_id in (
  '-1001466097052',  -- CoinList Official
  '-1001573866702',  -- COINVEST
  '-1001389304944',  -- Crypto Daily
  '-1001323464618',  -- DAO KONDR VC
  '-1001797009666',  -- FOMO Trade
  '-1001233043722',  -- Gram of TON
  '-1001288238074',  -- INVEST ZONE
  '-1001440288518',  -- KondrashovInvest
  '-1001530509454',  -- The Daily TON
  '-1001661945787',  -- TON CIS Hub
  '-1001662609289',  -- TON Contests
  '-1001755815351',  -- TON Dev News
  '-1002098196399',  -- TON Hunt
  '-1002055699842',  -- TON Research
  '-1001381169633',  -- Trade Father
  '-1001560620044',  -- TTC | TimeToCrypto
  '-1001821971377',  -- Vlasov - Invest
  '-1001353331991',  -- КриптоБезумие
  '-1001414438746',  -- Маришка инвестирует
  '-1001570859949',  -- Мой путь к 1🍋$ в крипте
  '-1002084116026'   -- Трейдерские заметки | Профессор Клинков
);

-- Channels most forwarded into notes over the last 12 months
update sources set weight = 1.2 where kind = 'telegram' and external_id in (
  '-1001684146975',  -- Анализ данных (Data analysis)
  '-1001114591086',  -- Machinelearning
  '-1001020923877',  -- Hacker News
  '-1001614688599',  -- Нейронавт
  '-1001639833661',  -- GitHub Community
  '-1001720833502',  -- Data Secrets
  '-1001480197231',  -- Поляков считает
  '-1002546853359',  -- Никита Велс | AI
  '-1002553901043'   -- Глеб Кудрявцев — мастер AI
);

-- Feeds that refuse the server's IP
update sources set enabled = false where kind = 'rss' and external_id in (
  'https://allenai.org/rss.xml'  -- Ai2: 403 from the datacenter IP, fine from home
);
