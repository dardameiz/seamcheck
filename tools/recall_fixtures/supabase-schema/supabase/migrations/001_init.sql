create table projects (id uuid primary key, name text, owner_id uuid);
create table tasks (id uuid primary key, title text, assigned_to uuid);
alter table projects enable row level security;
create policy projects_own on projects for select using (owner_id = auth.uid());
create function get_summary(org int) returns json as $$ select '{}'::json $$ language sql;
