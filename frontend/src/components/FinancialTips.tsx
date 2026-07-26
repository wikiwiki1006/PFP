import { useState, useEffect } from 'react'

const TIPS = [
  { term: 'VIX (공포지수)',        desc: '시장 불안 수준을 수치로 나타낸 지표. 20 이상이면 불안, 30 이상이면 공포 상태로 봅니다.' },
  { term: 'Beta (베타)',           desc: '주식이 시장 대비 얼마나 출렁이는지를 나타냅니다. 베타 1.5면 시장이 1% 오를 때 1.5% 반응합니다.' },
  { term: '기준금리',              desc: '중앙은행이 결정하는 기본 이자율. 금리가 오르면 주식보다 예금·채권의 매력이 높아집니다.' },
  { term: '채권 (Bond)',           desc: '정부나 기업이 돈을 빌리고 발행하는 증서. 금리가 오르면 채권 가격은 내려갑니다.' },
  { term: '달러 인덱스 (DXY)',      desc: '달러 강세를 주요 통화 대비로 측정하는 지수. DXY가 오르면 원화·신흥국 통화는 약해집니다.' },
  { term: '국채 수익률 (Yield)',    desc: '국채 보유 시 받는 이자율. 높아지면 주식의 상대적 매력이 줄어 주가에 부담이 됩니다.' },
  { term: 'WTI 원유',             desc: '미국 대표 원유 가격 기준. 유가 상승 시 에너지주 수혜, 항공·운송업엔 비용 부담이 됩니다.' },
  { term: '연준 (Fed)',            desc: '미국 중앙은행. 금리를 올리면(긴축) 시장 유동성 감소, 내리면(완화) 자금이 풀립니다.' },
  { term: 'P/E 비율 (주가수익비율)', desc: '주가가 순이익의 몇 배인지 나타내는 지표. 높을수록 기대치가 높거나 고평가된 상태입니다.' },
  { term: '섹터 로테이션',          desc: '경기 사이클에 따라 유망 업종이 바뀌는 현상. 경기 회복기엔 경기민감주, 침체기엔 방어주가 강합니다.' },
  { term: '인플레이션',             desc: '물가가 지속적으로 오르는 현상. 높은 인플레이션은 금리 인상 압력으로 이어져 주식시장에 부담을 줍니다.' },
  { term: '달러-원 환율',           desc: '달러 대비 원화 가치. 환율이 오르면(원화 약세) 수출주에 유리하고 수입 비용은 올라갑니다.' },
]

export function FinancialTips() {
  const [idx, setIdx]         = useState(() => Math.floor(Math.random() * TIPS.length))
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    const id = setInterval(() => {
      setVisible(false)
      setTimeout(() => {
        setIdx(i => (i + 1) % TIPS.length)
        setVisible(true)
      }, 350)
    }, 4000)
    return () => clearInterval(id)
  }, [])

  const tip = TIPS[idx]

  return (
    <div
      className="px-3 py-2 rounded border border-[#1e2d40] bg-[#06091200]"
      style={{ transition: 'opacity 0.35s ease', opacity: visible ? 1 : 0 }}
    >
      <span className="text-[10px] font-bold text-[#9b59b6] mr-2">{tip.term}</span>
      <span className="text-[10px] text-[#64748b]">{tip.desc}</span>
    </div>
  )
}
