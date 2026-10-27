-- mPOS device control: which mPOS terminals may bill, who changed it, and every phone check-in,
-- in Snowflake (V2RETAIL.BRONZE) beside the site users. Billing data itself stays in SQL Server.
-- Created on first use by backend/mpos/devices/store.py. Safe to re-run (IF NOT EXISTS).
-- Snowflake does not enforce PRIMARY KEY - the site keeps keys unique itself.
-- No semicolons inside comments: the site splits this file on them.

-- The all-mPOS switch: one row, ID = 1.
CREATE TABLE IF NOT EXISTS V2RETAIL.BRONZE.MPOS_GLOBAL (
    ID          NUMBER        NOT NULL PRIMARY KEY,
    ENABLED     BOOLEAN       NOT NULL DEFAULT TRUE,
    REASON      VARCHAR(500),
    VERSION     NUMBER        NOT NULL DEFAULT 1,       -- bumped on every change, guards against two admins overwriting
    CHANGE_SEQ  NUMBER        NOT NULL DEFAULT 0,       -- bumped on ANY switch change anywhere: the site reloads when it moves
    UPDATED_AT  TIMESTAMP_NTZ,
    UPDATED_BY  VARCHAR(50)
);

MERGE INTO V2RETAIL.BRONZE.MPOS_GLOBAL g USING (SELECT 1 AS ID) s ON g.ID = s.ID
    WHEN NOT MATCHED THEN INSERT (ID) VALUES (1);

-- One row per store that has (or had) mPOS terminals.
CREATE TABLE IF NOT EXISTS V2RETAIL.BRONZE.MPOS_STORES (
    STORE_CODE      VARCHAR(20)   NOT NULL PRIMARY KEY,
    HO_ENABLED      BOOLEAN       NOT NULL DEFAULT TRUE,   -- HO's store switch, the store cannot override it
    HO_REASON       VARCHAR(500),
    DAY_CONTROL     BOOLEAN       NOT NULL DEFAULT FALSE,  -- TRUE = terminals bill only after Open day
    DAY_OPEN        BOOLEAN       NOT NULL DEFAULT FALSE,
    DAY_OPENED_AT   TIMESTAMP_NTZ,
    DAY_OPENED_BY   VARCHAR(50),
    DAY_CLOSE_TIME  VARCHAR(5),                            -- 'HH:MM' local time, NULL = closed by hand only
    APPROVER        VARCHAR(50),                           -- site username of the store's device approver
    MAX_MPOS        NUMBER        NOT NULL DEFAULT 5,      -- active terminals allowed, the code sets it on insert
    NEXT_SEQ        NUMBER        NOT NULL DEFAULT 1,      -- next terminal number, never reused
    VERSION         NUMBER        NOT NULL DEFAULT 1,
    CREATED_AT      TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    CREATED_BY      VARCHAR(50),
    UPDATED_AT      TIMESTAMP_NTZ,
    UPDATED_BY      VARCHAR(50)
);

-- One row per mPOS terminal, e.g. HD22_MPOS1. A retired terminal keeps its row (and its number).
CREATE TABLE IF NOT EXISTS V2RETAIL.BRONZE.MPOS_TERMINALS (
    TERMINAL_ID      VARCHAR(30)   NOT NULL PRIMARY KEY,
    STORE_CODE       VARCHAR(20)   NOT NULL,
    SEQ              NUMBER        NOT NULL,
    STATUS           VARCHAR(10)   NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE, RETIRED
    STORE_ENABLED    BOOLEAN       NOT NULL DEFAULT TRUE,      -- the store approver's switch
    HO_VETO          BOOLEAN       NOT NULL DEFAULT FALSE,     -- HO block: only HO can lift it
    VETO_REASON      VARCHAR(500),
    IMMEDIATE_LOCK   BOOLEAN       NOT NULL DEFAULT FALSE,     -- lost or stolen phone: stops even mid-bill
    LOCK_REASON      VARCHAR(500),
    TOKEN_HASH       VARCHAR(64),                              -- SHA-256 of the phone's token, NULL = not paired
    PAIR_CODE_HASH   VARCHAR(64),                              -- SHA-256 of the open pairing code
    PAIR_EXPIRES_AT  TIMESTAMP_NTZ,
    PAIR_ATTEMPTS    NUMBER        NOT NULL DEFAULT 0,
    DEVICE_SERIAL    VARCHAR(100),
    APP_VERSION      VARCHAR(30),
    PAIRED_AT        TIMESTAMP_NTZ,
    PAIRED_BY        VARCHAR(50),
    LAST_SEEN_AT     TIMESTAMP_NTZ,                            -- written in batches, not on every check-in
    VERSION          NUMBER        NOT NULL DEFAULT 1,
    CREATED_AT       TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    CREATED_BY       VARCHAR(50),
    UPDATED_AT       TIMESTAMP_NTZ,
    UPDATED_BY       VARCHAR(50)
);

-- Every change to a switch, every pairing, and phones going offline / coming back.
CREATE TABLE IF NOT EXISTS V2RETAIL.BRONZE.MPOS_DEVICE_HISTORY (
    EVENT_ID        NUMBER AUTOINCREMENT START 1 INCREMENT 1 PRIMARY KEY,
    EVENT_TIME      TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    ACTOR           VARCHAR(50),        -- site username, or the terminal ID for events a phone caused
    LEVEL           VARCHAR(10)   NOT NULL,  -- GLOBAL, STORE, TERMINAL
    TARGET          VARCHAR(30),        -- store code or terminal ID (NULL for GLOBAL)
    EVENT           VARCHAR(40)   NOT NULL,
    -- GLOBAL_ON, GLOBAL_OFF, STORE_ON, STORE_OFF, DAY_OPEN, DAY_CLOSE, APPROVER_SET, MAX_SET,
    -- TERMINAL_ADDED, TERMINAL_ON, TERMINAL_OFF, VETO_ON, VETO_OFF, LOCK_ON, LOCK_OFF,
    -- PAIR_CODE_ISSUED, PAIR_FAILED, PAIRED, RETIRED, DRAIN_DONE, OFFLINE, ONLINE
    OLD_VALUE       VARCHAR(200),
    NEW_VALUE       VARCHAR(200),
    REASON          VARCHAR(500),
    AFFECTED_COUNT  NUMBER        NOT NULL DEFAULT 1,  -- terminals the change reached
    IP_ADDRESS      VARCHAR(64)
);

-- Every phone check-in, appended in batches by the site (device health is reported from here).
CREATE TABLE IF NOT EXISTS V2RETAIL.BRONZE.MPOS_DEVICE_HEARTBEATS (
    TERMINAL_ID   VARCHAR(30)   NOT NULL,
    STORE_CODE    VARCHAR(20)   NOT NULL,
    SEEN_AT       TIMESTAMP_NTZ NOT NULL,
    BILL_OPEN     BOOLEAN       NOT NULL,
    STATE_GIVEN   VARCHAR(10)   NOT NULL,   -- ON, DRAINING, OFF: what the site answered
    APP_VERSION   VARCHAR(30),
    SERIAL        VARCHAR(100),
    IP_ADDRESS    VARCHAR(64),
    NETWORK       VARCHAR(20),              -- wifi / mobile, if the app sends it
    BATTERY_PCT   NUMBER(3)
);
