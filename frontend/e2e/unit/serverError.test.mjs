/**
 * 요청 실패 문장 — `components/serverError.ts` (서버 detail 우선).
 *
 * axios 의 `e.message` 는 "Request failed with status code 500" 이라 사용자가 할 수
 * 있는 일(기다리기·다시 하기·포기하기)을 가르지 못한다. 서버가 FastAPI detail 에
 * 사유를 실으면 그걸 먼저 보여 준다.
 *
 * 모양은 실제 axios 오류를 따른다 — `{ message, response: { status, data: { detail } } }`.
 */
import assert from 'node:assert/strict'
import test, { describe } from 'node:test'

import { errorText, serverDetail, stringDetail } from '../../src/components/serverError.ts'

const AXIOS_500 = 'Request failed with status code 500'

function axiosError(detail, { message = AXIOS_500, data } = {}) {
  const err = new Error(message)
  err.response = { status: 500, data: data ?? (detail === undefined ? {} : { detail }) }
  return err
}

describe('문자열 detail — 그 문장이 먼저다', () => {
  test('axios 메시지 대신 서버 사유', () => {
    const err = axiosError('기준일 종가가 아직 수집되지 않았습니다')
    assert.equal(errorText(err), '기준일 종가가 아직 수집되지 않았습니다')
    assert.equal(serverDetail(err), '기준일 종가가 아직 수집되지 않았습니다')
    assert.equal(stringDetail(err), '기준일 종가가 아직 수집되지 않았습니다')
  })

  test('앞뒤 공백은 떼고, 공백뿐이면 사유가 없는 것', () => {
    assert.equal(errorText(axiosError('  다시 시도하세요  ')), '다시 시도하세요')
    assert.equal(serverDetail(axiosError('   ')), null)
    assert.equal(errorText(axiosError('   ')), AXIOS_500)
  })
})

describe('배열 detail (FastAPI 검증 오류)', () => {
  const detail = [{ type: 'missing', loc: ['query', 'ticker'], msg: 'Field required' }]

  test('보여 줄 때는 JSON 문자열로 — 사유가 있다는 사실을 버리지 않는다', () => {
    const err = axiosError(detail)
    assert.equal(serverDetail(err), JSON.stringify(detail))
    assert.equal(errorText(err), JSON.stringify(detail))
  })

  test('문장만 원하는 자리에는 null — 사람이 읽을 문장이 아니다', () => {
    assert.equal(stringDetail(axiosError(detail)), null)
  })

  test('직렬화가 안 되는 객체도 오류를 던지지 않는다', () => {
    const circular = {}
    circular.self = circular
    assert.equal(typeof serverDetail(axiosError(circular)), 'string')
  })
})

describe('detail 이 없을 때', () => {
  test('응답은 있는데 detail 이 없으면 axios 메시지', () => {
    assert.equal(errorText(axiosError(undefined)), AXIOS_500)
    assert.equal(serverDetail(axiosError(undefined)), null)
  })

  test('응답 자체가 없으면(네트워크 오류) 그 메시지', () => {
    assert.equal(errorText(new Error('Network Error')), 'Network Error')
  })

  test('메시지도 없으면 호출자가 준 문장, 그것도 없으면 기본 문장', () => {
    assert.equal(errorText({}, '브리핑을 시작하지 못했습니다'), '브리핑을 시작하지 못했습니다')
    assert.equal(errorText(null), '요청에 실패했습니다.')
    assert.equal(errorText(undefined), '요청에 실패했습니다.')
    assert.equal(errorText({ message: '   ' }, 'fallback'), 'fallback')
  })

  test('detail 이 0·false 같은 값이면 그것도 사유다 — null 과 구별된다', () => {
    assert.equal(serverDetail(axiosError(0)), '0')
    assert.equal(serverDetail(axiosError(false)), 'false')
  })
})
