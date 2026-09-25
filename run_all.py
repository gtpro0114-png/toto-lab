"""하루 한 번 실행되는 전체 순서: 수집 → 예측·채점 → 사이트 생성."""
import collect, odds, predict, build_site

if __name__ == "__main__":
    collect.run()
    odds.run()
    predict.run()
    build_site.run()
