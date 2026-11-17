import { session } from '@/lib/server';
import { localEngine } from '@/lib/local-engine';
export const dynamic='force-dynamic';
const json=(d:unknown,status=200)=>Response.json(d,{status,headers:{'Cache-Control':'private, no-store'}});
export async function GET(req:Request){try{const user=await session(req);if(!user)return json({error:'Sign in first.'},401);if(user.role!=='controller')return json({error:'Control Centre access required.'},403);return json(await localEngine<Record<string,unknown>>('/model-comparison',{}))}catch(e){return json({error:(e as Error).message},503)}}
