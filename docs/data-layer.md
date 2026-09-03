# The data layer

The seam is a **name crossing a boundary nothing checks**. A route string is one instance.
Where you keep your data is another, and usually nobody is checking that at all.

### Supabase

Your client names a table, a column, a function and an edge function as **strings**, and
they are checked against `supabase/migrations/*.sql`.

```
UNRESOLVED  db_table_use       order              the migrations declare `orders`
UNRESOLVED  db_column_use      order.total        PostgREST returns the row WITHOUT it
UNRESOLVED  db_function_use    get_statistics     the function is `get_stats`
UNRESOLVED  edge_function_use  send-mail          the directory is `send-email`
UNUSED      db_table           audit_log          no client code touches it
UNRESOLVED  db_policy          orders             read by the client, RLS off
```

A mistyped **column** is the quiet one. PostgREST returns the rows without it, the client
reads `undefined`, and a blank field ships. Nothing raises. `supabase gen types` catches it
only if you regenerate after every migration — and that drift is the bug.

The last line is a security check: the anon key ships in your browser bundle, so a table the
client reads with row level security off is readable by anyone who opens devtools.

The schema reader is not Supabase-specific — it also reads `migrations/`, `db/migrate/`,
`database/migrations/` and `sql/`, so Alembic, dbmate, Sqitch and hand-rolled folders work.

### Firebase

`httpsCallable('sendEmail')` is checked against what your functions directory exports —
same shape as a fetch against a route.

Firestore is **schemaless**, so "does this collection exist" has no answer in your files and
is never claimed. But `firestore.rules` *is* a declaration: a collection with no `match`
block is denied by default and fails silently in production, and a `match` block for a
collection nobody touches is usually a rename left behind.

### Redis

No schema either, so a key is checked against its **counterpart**.

```
UNRESOLVED  redis_key  user:*:stat     read here, written nowhere — can only ever miss
UNUSED      redis_key  user:*:legacy   written here, read nowhere
UNRESOLVED  redis_ttl  cache:board:*   names itself a cache, written with no expiry
```

Key patterns are normalised before they are compared, so `user:{uid}:stats`,
`user:${id}:stats` and `user:%s:stats` are one key — a Python writer meets a JavaScript
reader.

On the map, each store is a layer across every page — see
[A store, across every page](the-map.md#a-store-across-every-page).
