import { style } from '@react-spectrum/s2/style' with {type: 'macro'};
import { useState } from 'react';
import KlineViewContainer from '../charting/view/KlineViewContainer';

type HomePageProps = {
    toggleColorTheme?: () => void
    colorTheme?: 'light' | 'dark'
}

import { useNavigate } from 'react-router';

// ...

const HomePage = (props: HomePageProps) => {
    const navigate = useNavigate();
    const [isChartVisible, setIsChartVisible] = useState(false);
    // Basic responsive width - hardcoded subtraction for padding/scrollbars
    // Ideally use ResizeObserver or just 100% and let CSS handle it, but KlineViewContainer uses number width prop.
    const width = window.innerWidth;

    return (
        <div className={style({ display: "flex", width: "100%", height: "100%", position: "relative", overflow: "hidden" })}>
            <KlineViewContainer
                width={width}
                toggleColorTheme={props.toggleColorTheme}
                colorTheme={props.colorTheme}
                navigate={navigate}
                chartPrivacyHidden={!isChartVisible}
            />
            <button
                className={`homepage-privacy-toggle ${!isChartVisible ? "is-hidden" : ""}`}
                onClick={() => setIsChartVisible((prev) => !prev)}
                type="button"
            >
                {isChartVisible ? "隐藏图表" : "显示图表"}
            </button>
        </div>)
};

export default HomePage;
