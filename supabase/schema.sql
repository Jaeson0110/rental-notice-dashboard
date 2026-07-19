-- Supabase SQL Editor에서 전체 실행하세요.
-- 실행 후 맨 아래 INSERT문의 이메일 두 개를 실제 주소로 바꾸어 실행합니다.

create table if not exists public.allowed_users (
  email text primary key,
  display_name text,
  created_at timestamptz not null default now()
);

create table if not exists public.notice_states (
  notice_id text primary key,
  favorite boolean not null default false,
  stage text not null default '관심 없음'
    check (stage in ('관심 없음', '검토 필요', '신청 예정', '서류 준비 중', '신청 완료', '제외')),
  memo text not null default '',
  updated_by text,
  updated_at timestamptz not null default now()
);

alter table public.allowed_users enable row level security;
alter table public.notice_states enable row level security;

create or replace function public.is_allowed_user()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.allowed_users
    where lower(email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  );
$$;

revoke all on function public.is_allowed_user() from public;
grant execute on function public.is_allowed_user() to authenticated;

create policy "allowed user can read own allowlist row"
on public.allowed_users
for select
to authenticated
using (lower(email) = lower(coalesce(auth.jwt() ->> 'email', '')));

create policy "allowed users can read shared notice states"
on public.notice_states
for select
to authenticated
using (public.is_allowed_user());

create policy "allowed users can insert shared notice states"
on public.notice_states
for insert
to authenticated
with check (public.is_allowed_user());

create policy "allowed users can update shared notice states"
on public.notice_states
for update
to authenticated
using (public.is_allowed_user())
with check (public.is_allowed_user());

create policy "allowed users can delete shared notice states"
on public.notice_states
for delete
to authenticated
using (public.is_allowed_user());

-- 실시간 동기화를 켭니다. 이미 추가되어 있으면 오류가 날 수 있으며, 그 경우 무시해도 됩니다.
alter publication supabase_realtime add table public.notice_states;

-- 아래 이메일을 실제 두 사람의 이메일로 바꾼 뒤 주석을 해제하고 실행하세요.
-- insert into public.allowed_users (email, display_name) values
--   ('first@example.com', '재웅'),
--   ('second@example.com', '여자친구')
-- on conflict (email) do update set display_name = excluded.display_name;
