/** Format offsets in minutes from the planning start, relative to the visible day. */
export function clockMinute(value: number): string {
 const minute=((value%1440)+1440)%1440;
 return `${String(Math.floor(minute/60)).padStart(2,'0')}:${String(minute%60).padStart(2,'0')}`;
}
export function timelineTime(value: number, visibleDay: number): string {
 const difference=Math.floor(value/1440)-visibleDay;
 const prefix=difference===-1?'Previous day ':difference===1?'Next day ':difference<0?`${-difference} days earlier `:difference>0?`${difference} days later `:'';
 return prefix+clockMinute(value);
}
