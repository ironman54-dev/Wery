-- The Capital Code site: one backend for every waitlist and the Contact form.
-- APPLIED 24 Nov 2026 to Supabase project mmbdijtyvgzhmnlcddki (capital-code-site, Mumbai). Safe to re-run.
-- Safe to re-run.

create table if not exists public.cc_waitlist (
  id          bigserial primary key,
  product     text not null check (product in ('nexus','codeflow','greekwatch')),
  name        text not null default '',
  email       text not null,
  mobile      text,
  own_code    text not null unique,
  ref_code    text,                 -- invite code the person joined with (only kept if it is a real code)
  priority    boolean not null default false,   -- joined with a valid invite code: let in before the open queue
  suggestion  text,
  created_at  timestamptz not null default now(),
  unique (product, email)
);
create table if not exists public.cc_contact (
  id          bigserial primary key,
  name        text not null,
  email       text not null,
  topic       text,
  message     text not null,
  created_at  timestamptz not null default now()
);
alter table public.cc_waitlist enable row level security;
alter table public.cc_contact  enable row level security;
-- no policies on purpose: nobody reads or writes the tables directly; only the functions below, which run as owner.

create or replace function public.cc_join_waitlist(
  p_product text, p_name text, p_email text, p_mobile text default null, p_ref text default null, p_suggestion text default null)
returns json language plpgsql security definer set search_path = public as $$
declare
  v_email text := lower(btrim(p_email));
  v_ref   text := nullif(upper(btrim(coalesce(p_ref,''))),'');
  v_valid boolean := false;
  v_row   public.cc_waitlist;
  v_code  text;
  v_place int;
  v_refs  int;
begin
  if p_product not in ('nexus','codeflow','greekwatch') then raise exception 'bad product'; end if;
  if v_email !~ '^[^\s@]+@[^\s@]+\.[^\s@]+$' or length(v_email) > 200 then raise exception 'bad email'; end if;
  if v_ref is not null then
    select exists(select 1 from public.cc_waitlist where own_code = v_ref and product = p_product) into v_valid;
    if not v_valid then v_ref := null; end if;
  end if;

  select * into v_row from public.cc_waitlist where product = p_product and email = v_email;
  if not found then
    loop
      v_code := upper(substr(md5(random()::text || clock_timestamp()::text), 1, 6));
      exit when not exists(select 1 from public.cc_waitlist where own_code = v_code);
    end loop;
    insert into public.cc_waitlist(product, name, email, mobile, own_code, ref_code, priority, suggestion)
    values (p_product, left(coalesce(p_name,''),120), v_email, left(p_mobile,20), v_code, v_ref, v_ref is not null, left(p_suggestion,1000))
    returning * into v_row;
  elsif p_suggestion is not null then
    update public.cc_waitlist set suggestion = left(p_suggestion,1000) where id = v_row.id returning * into v_row;
  end if;

  select count(*) into v_refs from public.cc_waitlist where product = p_product and ref_code = v_row.own_code;
  -- order: invite-code joiners first, then by time; each friend who joined with your code moves you up 50
  select greatest(1, 1 + count(*) - 50 * v_refs)::int into v_place
  from public.cc_waitlist w
  where w.product = p_product and w.id <> v_row.id
    and ((w.priority and not v_row.priority) or (w.priority = v_row.priority and w.created_at < v_row.created_at));
  return json_build_object('place', v_place, 'code', v_row.own_code, 'priority', v_row.priority);
end $$;

create or replace function public.cc_waitlist_count(p_product text)
returns int language sql security definer set search_path = public stable as
$$ select count(*)::int from public.cc_waitlist where product = p_product $$;

create or replace function public.cc_contact_submit(p_name text, p_email text, p_topic text, p_message text)
returns json language plpgsql security definer set search_path = public as $$
begin
  if length(btrim(coalesce(p_name,''))) < 2 or length(btrim(coalesce(p_message,''))) < 3 then raise exception 'missing fields'; end if;
  if p_email !~ '^[^\s@]+@[^\s@]+\.[^\s@]+$' then raise exception 'bad email'; end if;
  insert into public.cc_contact(name, email, topic, message)
  values (left(btrim(p_name),120), lower(btrim(p_email)), left(p_topic,120), left(p_message,4000));
  return json_build_object('ok', true);
end $$;

revoke all on function public.cc_join_waitlist(text,text,text,text,text,text) from public;
revoke all on function public.cc_waitlist_count(text) from public;
revoke all on function public.cc_contact_submit(text,text,text,text) from public;
grant execute on function public.cc_join_waitlist(text,text,text,text,text,text) to anon;
grant execute on function public.cc_waitlist_count(text) to anon;
grant execute on function public.cc_contact_submit(text,text,text,text) to anon;
