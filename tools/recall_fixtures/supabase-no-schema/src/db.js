import { createClient } from '@supabase/supabase-js'
export const supabase = createClient(process.env.URL, process.env.KEY)
export const listProjects = () => supabase.from('projects').select('id, name, created_at')
export const listTasks    = () => supabase.from('tasks').select('id, title, status')
export const summary      = () => supabase.rpc('get_org_summary', { org: 1 })
