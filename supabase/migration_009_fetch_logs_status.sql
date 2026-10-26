-- ============================================================
-- 聚成公司后台 · 迁移 009：放宽 fetch_logs.status 约束（指令61 A2）
-- 对已上线库执行本文件一次即可；全部语句幂等，可重复执行。
-- 背景：schema.sql 基础约束为 ('success','failed','pending',
-- 'skipped')，不含 need_user_login。但 /status 页面 STATUS_LABEL
-- 已有 need_user_login（待登录），且《10 月抓取运行单》§6 要求自动
-- 抓取会话失效时向 fetch_logs 写入 status='need_user_login'——不先
-- 放宽约束，B1 首抓后写失效日志会直接被约束拒绝。本迁移只放宽
-- 取值集，不改表结构、不改 RLS、不动存量数据，为 B1/B2 铺路。
--
-- status 取值对位表（DB 约束 ↔ /status STATUS_LABEL ↔ 抓取管道用词）：
--   success         成功    ← auto_fetch 结果 ok（抓取完成并落盘）
--   pending         待启用  ← 管道未启用占位（seed 现行行）
--   need_user_login 待登录  ← auto_fetch 结果 need_user_login（会话
--                             失效停手，见 docs/fetch-pipeline/运行单
--                             §6 与《设计_对账卡自动翻转》§2 状态机）
--   failed          失败    ← auto_fetch 结果 export_incomplete /
--                             validation_failed / error（细类写 message）
--   skipped         跳过    ← 本轮按规则跳过（如无新增区间）
--
-- 状态：本文件随仓交付时**尚未在生产库执行**——待老板在 Supabase
-- 控制台 SQL Editor 执行一次（与 migration_008 同流程）；执行前
-- 线上约束仍为旧四值，任何写入 need_user_login 的代码路径不得上线
-- （B2 翻转开发的前置条件，见《设计_对账卡自动翻转》§7 第 1 项）。
-- ============================================================

-- 旧约束为 schema.sql 内联未命名 check，Postgres 自动命名为
-- fetch_logs_status_check；先删后加以保证幂等与取值集准确。
alter table public.fetch_logs
  drop constraint if exists fetch_logs_status_check;

alter table public.fetch_logs
  add constraint fetch_logs_status_check
  check (status in ('success', 'failed', 'pending', 'skipped', 'need_user_login'));
