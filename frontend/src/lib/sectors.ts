/**
 * lib/sectors.ts
 * ──────────────
 * 영문 GICS 섹터명 → 화면 표기.
 *
 * 이 표가 두 곳에 있었다 (PairsTradingPanel · 매매신호 패널이 쓰려던 사본).
 * 같은 규칙을 두 곳에 두면 한쪽만 고쳐지는 것이 이 리포에서 반복된 형태라
 * (CLAUDE.md §6) 한 곳으로 모은다. 백엔드의 정규화는
 * `backend/services/sector_lookup.py` 의 `_GICS` 가 맡는다 — 그쪽은 표기를
 * GICS 표준으로 접는 일이고, 여기는 그 결과를 한국어로 보여 주는 일이다.
 */
import { getMarket } from './market'

const SECTOR_KO: Record<string, string> = {
  'Technology': '기술', 'Healthcare': '헬스케어', 'Financial Services': '금융',
  'Financials': '금융', 'Financial': '금융',
  'Consumer Cyclical': '경기소비재', 'Consumer Discretionary': '경기소비재',
  'Consumer Defensive': '필수소비재', 'Consumer Staples': '필수소비재',
  'Consumer': '소비재', 'Energy': '에너지', 'Industrials': '산업재',
  'Basic Materials': '소재', 'Materials': '소재',
  'Real Estate': '부동산', 'Utilities': '유틸리티',
  'Communication Services': '커뮤니케이션',
}

/** 한국 화면에서는 한글로, 미국 화면에서는 원문 그대로.
 *
 *  사전에 없는 값은 **지어내지 않고 받은 그대로** 보여 준다. 서버가
 *  '미분류'(섹터를 아직 확인하지 못함)를 보내면 그 말이 그대로 나간다. */
export const toKoSector = (s?: string | null): string =>
  !s ? '' : (getMarket() === 'KR' ? (SECTOR_KO[s] ?? s) : s)
