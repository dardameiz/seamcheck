import { createClient } from '@supabase/supabase-js'
export const supabase = createClient(process.env.URL, process.env.KEY)
export const listProjects = () => supabase.from('projects').select('id, name, owner_id')
export const listTasks    = () => supabase.from('tasks').select('id, title, assignee_id')
export const listGhosts   = () => supabase.from('ghost_table').select('id')
export const summary      = () => supabase.rpc('get_summary', { org: 1 })
export const missingRpc   = () => supabase.rpc('get_nothing', {})
