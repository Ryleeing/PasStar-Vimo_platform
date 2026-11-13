-- The Pass · SQLite schema
-- 静态表（BE2，由 scripts/preprocess.py 生成，运行时只读）

CREATE TABLE products (
  id                    TEXT PRIMARY KEY,          -- Valio 商品编码
  name_en               TEXT NOT NULL,
  name_fi               TEXT,
  name_sv               TEXT,
  brand                 TEXT,
  vendor                TEXT NOT NULL,
  category              TEXT NOT NULL,             -- 品类编码
  sales_unit            TEXT NOT NULL,             -- 如 KI、ST
  base_unit             TEXT NOT NULL,
  lot_size              REAL NOT NULL,             -- 订购步长（销售单位）
  units_json            TEXT NOT NULL,             -- [{unitId, sizeInBaseUnits, gtin}]
  gtin                  TEXT,
  temperature_condition INTEGER,
  min_temp_c            REAL,
  max_temp_c            REAL,
  country               TEXT,                      -- ISO alpha-2
  qty_per_sales_unit    REAL,                      -- 每个销售单位折合多少 L 或 kg
  qty_unit              TEXT CHECK (qty_unit IN ('L','kg','pcs')),
  qty_source            TEXT CHECK (qty_source IN ('UNIT_CONVERSIONS','NAME_PARSE','UNKNOWN')),
  case_volume_l         REAL,                      -- 外箱体积（升），用于冷库空间提示
  deposits_json         TEXT NOT NULL DEFAULT '[]',
  substitutions_json    TEXT NOT NULL DEFAULT '[]',-- 官方替代：["464440", ...]
  marketing_text_en     TEXT,                      -- 不可信输入：只展示，不参与排序
  has_synkka            INTEGER NOT NULL,          -- 是否有制造商数据
  synkka_updated_at     TEXT,
  delivery_reliability  REAL,                      -- 0–1，L2：对外只给等级
  co2_kg_per_kg         REAL                       -- 品类平均值
);

CREATE TABLE categories (
  code    TEXT PRIMARY KEY,
  name_en TEXT,
  name_fi TEXT
);

CREATE TABLE product_allergens (
  product_id  TEXT NOT NULL REFERENCES products(id),
  code        TEXT NOT NULL,                       -- GS1 代码，如 AM、AN、AP
  status      TEXT NOT NULL CHECK (status IN ('CONTAINS','MAY_CONTAIN','FREE_FROM','UNDECLARED')),
  trust_grade TEXT NOT NULL CHECK (trust_grade IN ('A','B','C','D')),
  PRIMARY KEY (product_id, code)
);

CREATE TABLE product_claims (
  product_id TEXT NOT NULL REFERENCES products(id),
  claim      TEXT NOT NULL,                        -- 如 FREE_FROM_LACTOSE、VEGAN
  source     TEXT NOT NULL CHECK (source IN ('STRUCTURED','NAME_ONLY')),
  PRIMARY KEY (product_id, claim)
);

CREATE TABLE product_labels (
  product_id TEXT NOT NULL REFERENCES products(id),
  label      TEXT NOT NULL,                        -- 如 EU_ORGANIC_FARMING、SVANEN
  PRIMARY KEY (product_id, label)
);

CREATE TABLE product_flags (
  product_id TEXT NOT NULL REFERENCES products(id),
  flag       TEXT NOT NULL,                        -- 如 NAME_CLAIM_CONFLICT、NO_SYNKKA、VOLUME_UNKNOWN
  detail     TEXT,
  PRIMARY KEY (product_id, flag)
);

CREATE VIRTUAL TABLE products_fts USING fts5(id UNINDEXED, name_en, name_fi, name_sv, brand);

CREATE INDEX idx_products_category ON products(category);
CREATE INDEX idx_claims_claim ON product_claims(claim);

-- 业务表

-- BE1
CREATE TABLE buyers (
  id               TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  delivery_address TEXT
);

CREATE TABLE agents (
  id           TEXT PRIMARY KEY,
  buyer_id     TEXT NOT NULL REFERENCES buyers(id),
  name         TEXT NOT NULL,
  api_key_hash TEXT NOT NULL,
  public_key   TEXT,
  status       TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','BLOCKED'))
);

CREATE TABLE protocols (
  id                 TEXT PRIMARY KEY,
  buyer_id           TEXT NOT NULL REFERENCES buyers(id),
  agent_id           TEXT NOT NULL REFERENCES agents(id),
  version            INTEGER NOT NULL DEFAULT 1,
  status             TEXT NOT NULL CHECK (status IN ('DRAFT','PENDING_BUYER_SIGNATURE','PENDING_VALIO_CONFIRMATION','ACTIVE','REVOKED','EXPIRED','SUPERSEDED')),
  body_json          TEXT NOT NULL,                -- ProtocolBody
  open_questions_json TEXT NOT NULL DEFAULT '[]',
  jws                TEXT,                         -- 生效后的签名
  supersedes         TEXT REFERENCES protocols(id),
  created_at         TEXT NOT NULL,
  buyer_signed_at    TEXT,
  valio_confirmed_at TEXT
);

-- BE3
CREATE TABLE profiles (
  id         TEXT PRIMARY KEY,
  buyer_id   TEXT NOT NULL REFERENCES buyers(id),
  name       TEXT NOT NULL,
  body_json  TEXT NOT NULL,                        -- ProfileBody
  updated_at TEXT NOT NULL
);

CREATE TABLE requests (
  id              TEXT PRIMARY KEY,
  buyer_id        TEXT NOT NULL REFERENCES buyers(id),
  protocol_id     TEXT NOT NULL REFERENCES protocols(id),
  profile_id      TEXT REFERENCES profiles(id),
  input_mode      TEXT NOT NULL CHECK (input_mode IN ('NATURAL_LANGUAGE','PANEL')),
  raw_text        TEXT,
  structured_json TEXT NOT NULL,                   -- StructuredRequest（确认后）
  status          TEXT NOT NULL CHECK (status IN ('CANDIDATES_READY','NO_CANDIDATES','QUOTED','ORDERED','CLOSED')),
  current_attempt INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL
);

CREATE TABLE chain_nodes (
  id              TEXT PRIMARY KEY,
  request_id      TEXT NOT NULL REFERENCES requests(id),
  node            INTEGER NOT NULL CHECK (node BETWEEN 1 AND 4),
  attempt         INTEGER NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('OK','REJECTED','SUPERSEDED')),
  summary         TEXT NOT NULL,                   -- 用户能看懂的一句话
  timeline_json   TEXT NOT NULL,                   -- [ChainEvent]
  constraints_json TEXT NOT NULL DEFAULT '[]',     -- 用户否定后加入的约束
  reject_reason   TEXT,
  created_at      TEXT NOT NULL
);

CREATE TABLE candidates (
  id              TEXT PRIMARY KEY,
  request_id      TEXT NOT NULL REFERENCES requests(id),
  attempt         INTEGER NOT NULL,
  rank            INTEGER,                         -- 1–8；标红的为 NULL
  badge           TEXT CHECK (badge IN ('RECOMMENDED','CHEAPEST','MOST_RELIABLE')),
  selectable      INTEGER NOT NULL,                -- 违规的为 0
  body_json       TEXT NOT NULL                    -- Candidate（含卡片）
);

-- BE1
CREATE TABLE quotes (
  id           TEXT PRIMARY KEY,
  request_id   TEXT NOT NULL REFERENCES requests(id),
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  body_json    TEXT NOT NULL,                      -- QuoteBody
  jws          TEXT NOT NULL,
  valid_until  TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('VALID','EXPIRED','SUPERSEDED','USED')),
  created_at   TEXT NOT NULL
);

CREATE TABLE orders (
  id              TEXT PRIMARY KEY,
  request_id      TEXT NOT NULL REFERENCES requests(id),
  quote_id        TEXT NOT NULL REFERENCES quotes(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  status          TEXT NOT NULL CHECK (status IN ('PLACED','PENDING_BUYER_APPROVAL','PENDING_VALIO_REVIEW','REQUOTE_REQUIRED','REJECTED','CANCELLED')),
  gate_json       TEXT NOT NULL,                   -- GateDecision
  created_at      TEXT NOT NULL,
  placed_at       TEXT
);

CREATE TABLE approvals (
  id           TEXT PRIMARY KEY,
  order_id     TEXT REFERENCES orders(id),
  protocol_id  TEXT REFERENCES protocols(id),      -- 协议修改的审批
  kind         TEXT NOT NULL CHECK (kind IN ('BUYER','VALIO')),
  subject      TEXT NOT NULL CHECK (subject IN ('ORDER','PROTOCOL_CHANGE','DATA_ACCESS')),
  reason_codes_json TEXT NOT NULL,
  risk_tags_json    TEXT NOT NULL DEFAULT '[]',
  status       TEXT NOT NULL CHECK (status IN ('PENDING','APPROVED','APPROVED_WITH_CHANGES','REJECTED','ROUTED')),
  decided_by   TEXT,
  decided_at   TEXT,
  note         TEXT,
  created_at   TEXT NOT NULL
);

CREATE TABLE audit_log (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  at         TEXT NOT NULL,
  request_id TEXT,
  actor      TEXT NOT NULL,                        -- agent:<id> | user:<role> | system
  event      TEXT NOT NULL,                        -- 如 QUOTE_CREATED、GATE_DECIDED
  visibility TEXT NOT NULL CHECK (visibility IN ('BUYER','VALIO','BOTH')),
  payload_json TEXT NOT NULL,
  prev_hash  TEXT NOT NULL,
  hash       TEXT NOT NULL
);

-- BE2
CREATE TABLE disclosures (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  agent_id    TEXT NOT NULL,
  request_id  TEXT,
  product_id  TEXT,
  field       TEXT NOT NULL,
  sensitivity TEXT NOT NULL CHECK (sensitivity IN ('L0','L1','L2','L3'))
);

CREATE TABLE availability_probes (
  agent_id   TEXT NOT NULL,
  product_id TEXT NOT NULL,
  quantity   REAL NOT NULL,
  at         TEXT NOT NULL
);

CREATE TABLE faults (
  name        TEXT PRIMARY KEY CHECK (name IN ('PRICE_CHANGE','PARTIAL_STOCK','TIMEOUT','RATE_LIMIT','EMPTY_RESULT')),
  enabled     INTEGER NOT NULL DEFAULT 0,
  params_json TEXT NOT NULL DEFAULT '{}'
);
