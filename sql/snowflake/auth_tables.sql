-- MPOS Collection: site users and authentication history, in Snowflake (V2RETAIL.BRONZE).
-- The site connects with the SNOWFLAKE_* settings in .env. Site users are NOT Snowflake users.
-- Safe to re-run (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS V2RETAIL.BRONZE.MPOS_USERS (
    USERNAME              VARCHAR(50)   NOT NULL PRIMARY KEY,   -- stored lower-case
    FULL_NAME             VARCHAR(200)  NOT NULL,
    ROLES                 VARCHAR(500)  NOT NULL DEFAULT '',    -- comma-separated, e.g. 'SALE_POSTING,REPORTS'
    PASSWORD_HASH         VARCHAR(500)  NOT NULL,               -- scrypt$... never the password itself
    MUST_CHANGE_PASSWORD  BOOLEAN       NOT NULL DEFAULT TRUE,
    IS_ACTIVE             BOOLEAN       NOT NULL DEFAULT TRUE,
    CREATED_AT            TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    CREATED_BY            VARCHAR(50),
    UPDATED_AT            TIMESTAMP_NTZ,
    UPDATED_BY            VARCHAR(50),
    LAST_LOGIN_AT         TIMESTAMP_NTZ,
    LOCATIONS             VARCHAR(4000) NOT NULL DEFAULT ''     -- 'HO' = all stores, else store codes 'HD22,DH24'
);
-- Columns added after the first release are listed in backend/mpos/auth/store.py (ADDED_COLUMNS)
-- and added automatically when missing.

CREATE TABLE IF NOT EXISTS V2RETAIL.BRONZE.MPOS_AUTH_HISTORY (
    EVENT_ID    NUMBER AUTOINCREMENT START 1 INCREMENT 1 PRIMARY KEY,
    EVENT_TIME  TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    USERNAME    VARCHAR(50),        -- who the event is about (login name as typed for failed logins)
    ACTOR       VARCHAR(50),        -- who did it (same as USERNAME except for admin changes)
    EVENT       VARCHAR(40)   NOT NULL,
    -- LOGIN_SUCCESS, LOGIN_FAILED, LOGIN_LOCKED, LOGOUT, PASSWORD_CHANGED,
    -- USER_CREATED, USER_UPDATED, PASSWORD_RESET, USER_ACTIVATED, USER_DEACTIVATED
    DETAIL      VARCHAR(1000),
    IP_ADDRESS  VARCHAR(64),
    USER_AGENT  VARCHAR(500)
);
