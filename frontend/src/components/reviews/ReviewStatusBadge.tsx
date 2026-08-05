import { Link } from 'react-router-dom'
import Badge from '../ui/Badge'
const labels:Record<string,string>={unreviewed:'Unreviewed',reviewed:'Reviewed',favorite:'Favorite',maybe:'Maybe',rejected:'Rejected',needs_work:'Needs work'}
export default function ReviewStatusBadge({trackId,status='unreviewed',rating}:{trackId:number;status?:string;rating?:number|null}){const tone=status==='favorite'?'succeeded':status==='rejected'?'failed':status==='maybe'||status==='needs_work'?'pending':'info';return <Link to={`/listening?track_id=${trackId}`}><Badge tone={tone}>{labels[status]||'Unreviewed'}{rating?` · ${rating}/5`:''}</Badge></Link>}
