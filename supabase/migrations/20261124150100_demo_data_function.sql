-- reset_demo_data(): wipes the mock CRM and reseeds the five demo personas.
--
-- Charge dates are relative to now(), so refund windows stay valid no matter
-- when a reviewer opens the demo. The knowledge base and traces are untouched.
-- Callable only by the API's server connection (execute revoked from API roles).

create or replace function public.reset_demo_data()
returns void
language plpgsql
set search_path = ''
as $$
begin
  truncate table public.refunds, public.tickets, public.charges, public.customers restart identity cascade;

  insert into public.customers
    (public_id, persona_key, full_name, email, zip_code, tier, contract_status, renews_on)
  values
    ('cus_8Q2PRS', 'priya',  'Priya Shah',  'priya.shah@example.com',  '94107', 'pro',        'active',   (now() + interval '24 days')::date),
    ('cus_3M7LEE', 'marcus', 'Marcus Lee',  'marcus.lee@example.com',  '10001', 'free',       'active',   null),
    ('cus_5E1RZE', 'elena',  'Elena Ruiz',  'elena.ruiz@example.com',  '60601', 'enterprise', 'active',   (now() + interval '165 days')::date),
    ('cus_9T4BKR', 'tom',    'Tom Becker',  'tom.becker@example.com',  '73301', 'pro',        'past_due', (now() - interval '10 days')::date);

  insert into public.charges (public_id, customer_id, amount, description, status, created_at)
  select v.public_id, c.id, v.amount, v.description, v.status, now() - v.age
  from (values
    -- Priya: monthly Pro charge billed twice six days ago (the duplicate-charge scenario)
    ('ch_4KD1P0', 'priya',   29.00, 'Pro plan, monthly',                   'succeeded', interval '36 days'),
    ('ch_7HX2P1', 'priya',   29.00, 'Pro plan, monthly',                   'succeeded', interval '6 days'),
    ('ch_7HX2P2', 'priya',   29.00, 'Pro plan, monthly',                   'succeeded', interval '6 days' - interval '2 minutes'),
    -- Marcus: Free plan with a one-off add-on (add-ons are non-refundable on Free)
    ('ch_2MA9F1', 'marcus',   9.00, 'Storage add-on, 50 GB',               'succeeded', interval '10 days'),
    -- Elena: seat expansion above the Enterprise auto-approve limit
    ('ch_6ER0E1', 'elena', 4800.00, 'Enterprise plan, annual',             'succeeded', interval '200 days'),
    ('ch_6ER0E2', 'elena', 1200.00, 'Seat expansion, 10 seats (prorated)', 'succeeded', interval '12 days'),
    -- Tom: last month succeeded, this month failed, so the contract is past due
    ('ch_1TB5P1', 'tom',     29.00, 'Pro plan, monthly',                   'succeeded', interval '40 days'),
    ('ch_1TB5P2', 'tom',     29.00, 'Pro plan, monthly',                   'failed',    interval '10 days')
  ) as v (public_id, persona, amount, description, status, age)
  join public.customers c on c.persona_key = v.persona;
end;
$$;

revoke execute on function public.reset_demo_data() from public, anon, authenticated;

select public.reset_demo_data();
