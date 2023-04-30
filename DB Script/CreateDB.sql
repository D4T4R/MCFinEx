CREATE TABLE "PUBLIC"."STOCKS_RESULTS_FOR_CURRENT_QUARTER"
(
   COMPANYCODE varchar(100) NOT NULL,
   RESULT_DATE date NOT NULL,
   FINANCIAL_QUARTER varchar(10) NOT NULL,
   CONSTRAINT PK_STOCKS_RESULTS_FOR_CURRENT_QUARTER PRIMARY KEY (COMPANYCODE,RESULT_DATE,FINANCIAL_QUARTER)
)
;
CREATE TABLE "PUBLIC"."STOCK_EPS_QUARTERLY_VALUATIONS"
(
   COMPANYCODE varchar(25) PRIMARY KEY NOT NULL,
   Q_EPS_1 numeric,
   Q_EPS_2 numeric,
   Q_EPS_3 numeric,
   Q_EPS_4 numeric,
   Q_EPS_5 numeric,
   Q_AVG_GROWTH_1 numeric,
   Q_AVG_GROWTH_2 numeric,
   Q_AVG_GROWTH_3 numeric,
   Q_AVG_GROWTH_4 numeric,
   Q_EPS_GROWTH numeric,
   Q_FORWARD_EPS numeric,
   Q_CURRENT_PE numeric,
   Q_FORWARD_PE numeric,
   Q_DIFFERENCE_IN_PE_PCT numeric,
   Q_TARGET_PRICE numeric
)
;
CREATE TABLE "PUBLIC"."STOCK_EPS_YEARLY_VALUATIONS"
(
   COMPANYCODE varchar(25) PRIMARY KEY NOT NULL,
   Y_EPS_1 numeric,
   Y_EPS_2 numeric,
   Y_EPS_3 numeric,
   Y_EPS_4 numeric,
   Y_EPS_5 numeric,
   Y_AVG_GROWTH_1 numeric,
   Y_AVG_GROWTH_2 numeric,
   Y_AVG_GROWTH_3 numeric,
   Y_AVG_GROWTH_4 numeric,
   Y_EPS_GROWTH numeric,
   Y_FORWARD_EPS numeric,
   Y_CURRENT_PE numeric,
   Y_FORWARD_PE numeric,
   Y_DIFFERENCE_IN_PE_PCT numeric,
   Y_TARGET_PRICE numeric
)
;
CREATE TABLE "PUBLIC"."STOCK_EV2EBITDA_VALUATIONS"
(
   COMPANYCODE varchar(25) PRIMARY KEY NOT NULL,
   ENTERPRISE_VALUE1 numeric,
   ENTERPRISE_VALUE2 numeric,
   ENTERPRISE_VALUE3 numeric,
   ENTERPRISE_VALUE4 numeric,
   ENTERPRISE_VALUE5 numeric,
   EV_BY_EBITDA1 numeric,
   EV_BY_EBITDA2 numeric,
   EV_BY_EBITDA3 numeric,
   EV_BY_EBITDA4 numeric,
   EV_BY_EBITDA5 numeric,
   EBITDA1 numeric,
   EBITDA2 numeric,
   EBITDA3 numeric,
   EBITDA4 numeric,
   EBITDA5 numeric,
   EBITDA_GROWTH_1 numeric,
   EBITDA_GROWTH_2 numeric,
   EBITDA_GROWTH_3 numeric,
   EBITDA_GROWTH_4 numeric,
   AVERAGE_EBITDA_GROWTH numeric,
   EXPECTED_EBITDA numeric,
   FORCASTED_EV numeric,
   LONG_TERM_BORROWINGS numeric,
   TARGET_PRICE numeric,
   TARGET_PRICE_WITH_BORROWING numeric,
   ENTRY_PRICE_1BY4 numeric,
   ENTRY_PRICE__WITH_BORROWING_1BY4 numeric,
   ENTRY_PRICE_1BY3 numeric,
   ENTRY_PRICE__WITH_BORROWING_1BY3 numeric,
   DIFFERENCE numeric,
   DIFFERENCE_WITH_BORROWING numeric,
   OUTSTANDING_SHARES numeric
)
;
CREATE TABLE "PUBLIC"."STOCK_FINANCIALS"
(
   COMPANYCODE varchar(25) PRIMARY KEY NOT NULL,
   PROMOTER_SHARE_HOLDING numeric,
   PROMOTER_PLEDGE numeric,
   RESERVES numeric,
   CAPITAL numeric,
   OTHER_LIABILITY numeric,
   CURRENT_LIABILITY numeric,
   CURRENT_ASSET numeric,
   EBIT numeric,
   TOTAL_LIABILITY numeric,
   INVENTORY_TURNOVER_RATIO numeric,
   OPERATING_CASHFLOW numeric,
   INVESTING_CASHFLOW numeric,
   FINANCING_CASHFLOW numeric,
   FREE_CASHFLOW numeric,
   EARNING_PER_SHARE numeric,
   INDUSTRY_PE numeric,
   BOOK_VALUE_PER_SHARE numeric,
   DIVIDEND_YIELD numeric,
   MARKET_CAPITAL numeric,
   OUTSTANDING_SHARES_1 numeric,
   CURRENT_PRICE numeric
)
;
CREATE TABLE "PUBLIC"."STOCK_MASTER"
(
   COMPANYCODE varchar(25) PRIMARY KEY NOT NULL,
   ISIN_CODE varchar(25),
   COMPANY_NAME varchar(200),
   SECTOR varchar(100),
   CAPITAL numeric,
   LAST_UPDATED date,
   LAST_UPDATED_QUARTER varchar(10),
   SCANFORRESULTS varchar(1)
)
;