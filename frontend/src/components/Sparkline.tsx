export default function Sparkline({
  values,
  width = 90,
  height = 22,
}: {
  values: (number | null)[]
  width?: number
  height?: number
}) {
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v))
  if (nums.length < 2) return <span className="text-zinc-600">—</span>
  const min = Math.min(...nums)
  const max = Math.max(...nums)
  const span = max - min || 1
  const step = width / (nums.length - 1)
  const points = nums
    .map((v, i) => `${(i * step).toFixed(1)},${(height - ((v - min) / span) * (height - 2) - 1).toFixed(1)}`)
    .join(' ')
  const rising = nums[nums.length - 1] >= nums[0]
  return (
    <svg width={width} height={height} className="inline-block align-middle">
      <polyline
        points={points}
        fill="none"
        stroke={rising ? '#34d399' : '#f87171'}
        strokeWidth="1.5"
      />
    </svg>
  )
}
